"""The S3 endpoint override, and the production rule that makes it acceptable.

BACKLOG `1.24`. `S3_ENDPOINT_URL` exists so the ingestion chain can be exercised
against a local S3-compatible process (E2E-2) without an AWS account. It is not
an ordinary setting: it redirects **every read and write of customer document
bytes**. In production it is honoured for exactly the object stores decision
#14 names, Cloudflare R2 and Backblaze B2 (ticket 18), and refused for every
other host, so the tests that matter most here are the production ones: the
allowlist must admit those two suffixes on the parsed hostname and nothing
else, or this module would be documenting a redirect primitive rather than a
bounded seam.

The credentials belong in this module for the same reason. `_get_s3` is the one
place that decides who this process is to the object store, and both halves of
that decision, where the bytes go and which key signs for them, are settings the
operator has to get right. The final section pins them explicit: boto3's default
chain, which would try environment variables, a shared credentials file and the
instance metadata service in turn, is never consulted.

Why each test resets `storage_service._s3`
------------------------------------------
The client is memoised in a module global. A test that does not reset it asserts
against whatever client a *previous* test constructed, which would let the
"endpoint is passed" test pass while reading the "endpoint is absent" client.
The autouse fixture below is therefore load-bearing, not hygiene.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from app.services import storage_service
from app.services.storage_service import StorageNotConfigured

_ENDPOINT = "http://127.0.0.1:9000"
_KEY_ID = "test-access-key-id"
_SECRET = "test-secret-access-key"

#: The owner's own R2 host, the value S3_EXPECTED_ENDPOINT_HOST carries in
#: production (#133). Every production test names it explicitly, because the
#: whole point of the setting is that the guard has an account to compare
#: against rather than a provider to pattern-match.
_OUR_HOST = "ourownaccountid.r2.cloudflarestorage.com"


@pytest.fixture(autouse=True)
def _reset_client():
    """Drop the memoised boto3 client before and after every test."""
    storage_service._s3 = None
    yield
    storage_service._s3 = None


def _build_client(**setting_overrides):
    """Call _get_s3() with a faked boto3 and return the client kwargs used."""
    fake_boto3 = MagicMock()
    defaults = {
        "AWS_REGION": "us-east-1",
        "S3_ENDPOINT_URL": None,
        "ENVIRONMENT": "development",
        "S3_ACCESS_KEY_ID": _KEY_ID,
        "S3_SECRET_ACCESS_KEY": _SECRET,
        "S3_EXPECTED_ENDPOINT_HOST": "",
    }
    defaults.update(setting_overrides)
    with patch.dict("sys.modules", {"boto3": fake_boto3}):
        with patch.multiple(storage_service.settings, **defaults):
            storage_service._get_s3()
    assert fake_boto3.client.call_count == 1
    return fake_boto3.client.call_args


# --------------------------------------------------------------------------
# The default is the production path
# --------------------------------------------------------------------------


def test_without_the_override_boto3_gets_no_endpoint_url():
    """Every existing deployment must be unaffected by this field existing."""
    args, kwargs = _build_client(S3_ENDPOINT_URL=None)
    assert args == ("s3",)
    assert "endpoint_url" not in kwargs, (
        "boto3 was given an endpoint_url when S3_ENDPOINT_URL is unset. The "
        "default must resolve real AWS exactly as it did before this seam "
        f"existed. kwargs={kwargs}"
    )
    assert kwargs["region_name"] == "us-east-1"


def test_with_the_override_boto3_gets_the_endpoint_url():
    args, kwargs = _build_client(S3_ENDPOINT_URL=_ENDPOINT)
    assert kwargs.get("endpoint_url") == _ENDPOINT, (
        f"S3_ENDPOINT_URL was set but boto3 did not receive it. kwargs={kwargs}"
    )
    assert kwargs["region_name"] == "us-east-1"


# --------------------------------------------------------------------------
# The rule that justifies the seam
# --------------------------------------------------------------------------


def test_the_override_is_refused_in_production():
    """A production process may not redirect customer documents to an
    arbitrary endpoint; only the PRODUCTION_ENDPOINT_SUFFIXES stores pass.

    Refused loudly rather than ignored: an operator who set the variable
    believes the bytes are going somewhere. Silently sending them to AWS
    anyway is a different lie from silently sending them elsewhere, and both
    are worse than refusing to serve the path.
    """
    fake_boto3 = MagicMock()
    with patch.dict("sys.modules", {"boto3": fake_boto3}):
        with patch.multiple(
            storage_service.settings,
            AWS_REGION="us-east-1",
            S3_ENDPOINT_URL=_ENDPOINT,
            ENVIRONMENT="production",
        ):
            with pytest.raises(StorageNotConfigured) as exc_info:
                storage_service._get_s3()

    message = str(exc_info.value)
    assert "S3_ENDPOINT_URL" in message, (
        "the error must name the variable the operator has to unset; "
        f"got {message!r}"
    )
    assert fake_boto3.client.call_count == 0, (
        "a client was constructed anyway — the guard must refuse BEFORE "
        "building anything that could serve a request"
    )


def test_production_without_the_override_is_unaffected():
    """The guard is conditional on the override, not a ban on production."""
    args, kwargs = _build_client(S3_ENDPOINT_URL=None, ENVIRONMENT="production")
    assert args == ("s3",)
    assert "endpoint_url" not in kwargs


def _refused_in_production(endpoint: str, expected_host: str = _OUR_HOST) -> str:
    """Run _get_s3 in production with `endpoint` and return the refusal text."""
    fake_boto3 = MagicMock()
    with patch.dict("sys.modules", {"boto3": fake_boto3}):
        with patch.multiple(
            storage_service.settings,
            AWS_REGION="us-east-1",
            S3_ENDPOINT_URL=endpoint,
            ENVIRONMENT="production",
            S3_EXPECTED_ENDPOINT_HOST=expected_host,
        ):
            with pytest.raises(StorageNotConfigured) as exc_info:
                storage_service._get_s3()
    assert fake_boto3.client.call_count == 0, (
        "a client was constructed anyway — the guard must refuse BEFORE "
        "building anything that could serve a request"
    )
    return str(exc_info.value)


def test_r2_is_honoured_in_production():
    """Decision #14.6: R2 is a destination production may write documents to,
    at the one account S3_EXPECTED_ENDPOINT_HOST names."""
    r2 = f"https://{_OUR_HOST}"
    args, kwargs = _build_client(
        S3_ENDPOINT_URL=r2,
        ENVIRONMENT="production",
        S3_EXPECTED_ENDPOINT_HOST=_OUR_HOST,
    )
    assert kwargs.get("endpoint_url") == r2


def test_b2_is_honoured_in_production():
    b2_host = "s3.us-west-004.backblazeb2.com"
    b2 = f"https://{b2_host}"
    args, kwargs = _build_client(
        S3_ENDPOINT_URL=b2,
        ENVIRONMENT="production",
        S3_EXPECTED_ENDPOINT_HOST=b2_host,
    )
    assert kwargs.get("endpoint_url") == b2


def test_the_allowlist_reads_the_parsed_host_not_the_string():
    """A suffix in the query string must not admit an arbitrary host."""
    message = _refused_in_production(
        "https://evil.example/?redirect=.r2.cloudflarestorage.com"
    )
    assert "evil.example" in message


def test_a_lookalike_suffix_without_the_dot_is_refused():
    """Two lookalikes, two threat classes, both out: an attacker-registrable
    domain carrying the words, and a sibling inside Cloudflare's own domain
    that is still not the .r2. subtree."""
    _refused_in_production("https://evilr2xcloudflarestorage.com")
    _refused_in_production("https://evilr2.cloudflarestorage.com")


def test_the_scheme_is_https_or_nothing_in_production():
    """Cleartext transport of customer documents is refused outright."""
    message = _refused_in_production(
        "http://accountid.r2.cloudflarestorage.com"
    )
    assert "https" in message


def test_the_bare_apex_is_not_a_subdomain():
    """r2.cloudflarestorage.com itself is nobody's bucket endpoint."""
    _refused_in_production("https://r2.cloudflarestorage.com")


def test_a_trailing_dot_fqdn_is_refused():
    """host. and host are the same place to a resolver; the check must not
    be sidestepped by the dot."""
    _refused_in_production("https://accountid.r2.cloudflarestorage.com.")


def test_an_endpoint_with_no_host_is_refused():
    message = _refused_in_production("https:///bucket-path-only")
    assert "unreadable host" in message


def test_credentials_embedded_in_the_endpoint_are_refused_in_production():
    """A URL carrying userinfo is one log line from disclosing the key."""
    message = _refused_in_production(
        "https://AKIA:secret@bucket.r2.cloudflarestorage.com"
    )
    assert "credential" in message.lower()


def test_the_host_comparison_is_case_insensitive():
    r2 = "https://OurOwnAccountId.R2.CloudflareStorage.com"
    args, kwargs = _build_client(
        S3_ENDPOINT_URL=r2,
        ENVIRONMENT="production",
        S3_EXPECTED_ENDPOINT_HOST=_OUR_HOST,
    )
    assert kwargs.get("endpoint_url") == r2


# --------------------------------------------------------------------------
# The account bound (#133)
# --------------------------------------------------------------------------


def test_another_accounts_r2_host_is_refused_in_production():
    """The finding itself.

    The suffix list bounds the PROVIDER. Every R2 tenant on earth carries
    `.r2.cloudflarestorage.com`, so a mistyped or hostile S3_ENDPOINT_URL that
    passes the suffix check still names somebody else's bucket, and every
    customer document uploaded afterwards is written into it. The bound
    decision #14.6 wanted is the owner's own account, which only an equality
    check against a configured host can express.
    """
    message = _refused_in_production(
        "https://attackeraccountid.r2.cloudflarestorage.com"
    )
    assert "attackeraccountid.r2.cloudflarestorage.com" in message, (
        "the refusal must name the host the endpoint actually points at; "
        f"got {message!r}"
    )
    assert "S3_EXPECTED_ENDPOINT_HOST" in message, (
        "the refusal must name the setting that decides which account is ours; "
        f"got {message!r}"
    )


def test_a_b2_host_is_refused_when_the_owners_account_is_on_r2():
    """Both providers stay on the suffix list, so only equality separates them."""
    message = _refused_in_production("https://s3.us-west-004.backblazeb2.com")
    assert "s3.us-west-004.backblazeb2.com" in message


def test_an_unset_expected_host_refuses_in_production():
    """Fail closed, not open.

    An empty S3_EXPECTED_ENDPOINT_HOST is not "no opinion, let the suffix list
    decide". It is a production process that has never been told which account
    is ours, and it must refuse the redirect rather than fall back to the
    provider-wide check this issue exists to replace.
    """
    message = _refused_in_production(f"https://{_OUR_HOST}", expected_host="")
    assert "S3_EXPECTED_ENDPOINT_HOST" in message


def test_a_host_outside_both_providers_is_still_refused():
    """The suffix list survives as a second gate.

    An operator who sets S3_EXPECTED_ENDPOINT_HOST to their own MinIO box has
    made the equality check agree with itself. Decision #14.6 names two object
    stores, and that decision is still the one production runs under.
    """
    message = _refused_in_production(
        "https://minio.internal.example", expected_host="minio.internal.example"
    )
    assert "minio.internal.example" in message
    assert "R2" in message or "Backblaze" in message


# --------------------------------------------------------------------------
# The unset-bucket finding: 503, not a 500 that reads as a bug
# --------------------------------------------------------------------------


def test_unset_bucket_raises_storage_not_configured():
    """`S3_UPLOADS_BUCKET` defaults to "" and must not reach botocore."""
    with patch.multiple(storage_service.settings, S3_UPLOADS_BUCKET=""):
        with pytest.raises(StorageNotConfigured) as exc_info:
            storage_service._bucket()
    assert "S3_UPLOADS_BUCKET" in str(exc_info.value)


def test_a_configured_bucket_is_returned():
    with patch.multiple(storage_service.settings, S3_UPLOADS_BUCKET="wchats-uploads"):
        assert storage_service._bucket() == "wchats-uploads"


def test_put_bytes_raises_before_calling_s3_when_unconfigured():
    """The raise must happen before the network call, not after.

    `put_bytes` is called inside the upload route's tenant-DB transaction, so a
    failure that happens *after* an S3 write would leave bytes with no document
    row pointing at them.
    """
    fake_client = MagicMock()
    with patch.object(storage_service, "_get_s3", return_value=fake_client):
        with patch.multiple(storage_service.settings, S3_UPLOADS_BUCKET=""):
            with pytest.raises(StorageNotConfigured):
                storage_service.put_bytes("agent/doc.pdf", b"bytes")
    assert fake_client.put_object.call_count == 0, (
        "put_object was called with an unconfigured bucket"
    )


# --------------------------------------------------------------------------
# The credentials are explicit; boto3's default chain is never consulted
# --------------------------------------------------------------------------


def _refused_credentials(**setting_overrides) -> str:
    """Call _get_s3() with the given credential settings, return the refusal.

    Fails if a client was built anyway. A client constructed without explicit
    credentials is the exact defect this section exists to prevent: boto3 would
    then go looking for an identity in the environment.
    """
    fake_boto3 = MagicMock()
    defaults = {
        "AWS_REGION": "us-east-1",
        "S3_ENDPOINT_URL": None,
        "ENVIRONMENT": "development",
        "S3_ACCESS_KEY_ID": "",
        "S3_SECRET_ACCESS_KEY": "",
    }
    defaults.update(setting_overrides)
    with patch.dict("sys.modules", {"boto3": fake_boto3}):
        with patch.multiple(storage_service.settings, **defaults):
            with pytest.raises(StorageNotConfigured) as exc_info:
                storage_service._get_s3()
    assert fake_boto3.client.call_count == 0, (
        "a client was constructed without explicit credentials, so boto3 would "
        "fall back to its default chain"
    )
    return str(exc_info.value)


def test_configured_credentials_reach_boto3():
    """The point of the whole section: boto3 is told who we are, not asked."""
    assert storage_service._s3 is None, (
        "the autouse fixture did not clear the memoised client, so this test "
        "would assert against a client some earlier test built"
    )
    args, kwargs = _build_client()
    assert args == ("s3",)
    assert kwargs["aws_access_key_id"] == _KEY_ID
    assert kwargs["aws_secret_access_key"] == _SECRET
    assert storage_service._s3 is not None, "the built client was not memoised"


@pytest.mark.parametrize(
    "missing,present",
    [
        ("S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY"),
        ("S3_SECRET_ACCESS_KEY", "S3_ACCESS_KEY_ID"),
    ],
)
def test_one_missing_credential_is_refused_by_name(missing, present):
    """Half-configured is the shape a real deploy lands in, and NoCredentialsError
    from inside the first upload names nothing the operator can act on."""
    message = _refused_credentials(**{present: "set-by-the-operator"})
    assert missing in message, (
        f"the refusal must name the setting that is unset; got {message!r}"
    )
    assert present not in message, (
        f"{present} is set, so naming it sends the operator to the wrong "
        f"variable; got {message!r}"
    )


def test_both_missing_credentials_are_named_in_one_message():
    """One trip, not two: an operator who sets only the first one on the strength
    of the first error comes straight back for the second."""
    message = _refused_credentials()
    assert "S3_ACCESS_KEY_ID" in message
    assert "S3_SECRET_ACCESS_KEY" in message


def test_the_refusal_does_not_echo_the_secret():
    """The message travels to logs and, through the 503, towards a caller.

    Naming a setting is the whole job. Printing what is in it would put the
    signing key wherever the refusal lands.
    """
    message = _refused_credentials(S3_SECRET_ACCESS_KEY="leak-me-not")
    assert "leak-me-not" not in message, (
        f"the refusal echoed the secret value; got {message!r}"
    )
    assert "S3_ACCESS_KEY_ID" in message


def test_no_configuration_builds_a_client_without_both_credentials():
    """The regression pin.

    Deleting the _require_credentials() call, or dropping one kwarg from the
    dict, restores boto3's default chain: it would then read AWS_ACCESS_KEY_ID
    from the environment, or reach the instance metadata service, and the
    process would authenticate as an identity nobody configured here. Every
    setting combination that reaches boto3.client at all must carry both.
    """
    combinations = [
        {},
        {"S3_ENDPOINT_URL": _ENDPOINT},
        {"S3_ENDPOINT_URL": None, "ENVIRONMENT": "production"},
        {
            "S3_ENDPOINT_URL": f"https://{_OUR_HOST}",
            "ENVIRONMENT": "production",
            "S3_EXPECTED_ENDPOINT_HOST": _OUR_HOST,
        },
    ]
    for overrides in combinations:
        storage_service._s3 = None
        args, kwargs = _build_client(**overrides)
        assert "aws_access_key_id" in kwargs and "aws_secret_access_key" in kwargs, (
            f"boto3.client was built without explicit credentials for "
            f"{overrides!r}, so its default credential chain is back. "
            f"kwargs={kwargs}"
        )
# --------------------------------------------------------------------------
# The setting is not optional in production (#133)
# --------------------------------------------------------------------------


def _production_settings(**overrides):
    """Build the shipped Settings for production, reading no .env file.

    `_env_file=None` detaches the developer's real `.env`, so what this asserts
    is the validator rather than the machine. The ten fields with no default
    come from the environment `tests/conftest.py` set at import time.
    """
    from app.core.config import Settings

    return Settings(_env_file=None, ENVIRONMENT="production", **overrides)


def test_production_boot_without_the_expected_host_refuses(monkeypatch):
    """A production process that was never told which account is ours must not
    reach the point where it can write a customer document.

    Refusing here rather than at the first upload means the deploy fails, the
    operator reads one message naming one variable, and nothing has been sent
    anywhere in the meantime.
    """
    monkeypatch.delenv("S3_EXPECTED_ENDPOINT_HOST", raising=False)
    with pytest.raises(ValidationError) as exc_info:
        _production_settings()
    message = str(exc_info.value)
    assert "S3_EXPECTED_ENDPOINT_HOST" in message, (
        f"the boot refusal must name the missing setting; got {message!r}"
    )


def test_a_configured_expected_host_boots_in_production(monkeypatch):
    monkeypatch.delenv("S3_EXPECTED_ENDPOINT_HOST", raising=False)
    built = _production_settings(S3_EXPECTED_ENDPOINT_HOST=_OUR_HOST)
    assert built.S3_EXPECTED_ENDPOINT_HOST == _OUR_HOST


def test_development_boot_without_the_expected_host_is_unaffected(monkeypatch):
    """Local work has no owner account and needs none; the seam is MinIO there."""
    from app.core.config import Settings

    monkeypatch.delenv("S3_EXPECTED_ENDPOINT_HOST", raising=False)
    built = Settings(_env_file=None, ENVIRONMENT="development")
    assert built.S3_EXPECTED_ENDPOINT_HOST == ""


@pytest.mark.parametrize(
    "pasted",
    [
        "https://ourownaccountid.r2.cloudflarestorage.com",
        "ourownaccountid.r2.cloudflarestorage.com/wchats-uploads",
        "user:secret@ourownaccountid.r2.cloudflarestorage.com",
    ],
)
def test_the_expected_host_is_a_bare_host_not_a_url(monkeypatch, pasted):
    """The operator has the endpoint URL in the clipboard, and the wizard asks
    for it two lines earlier. A pasted URL would never equal a parsed hostname,
    so the guard would refuse every upload with a message about the endpoint
    while the real fault is in the other variable.
    """
    monkeypatch.delenv("S3_EXPECTED_ENDPOINT_HOST", raising=False)
    with pytest.raises(ValidationError) as exc_info:
        _production_settings(S3_EXPECTED_ENDPOINT_HOST=pasted)
    assert "S3_EXPECTED_ENDPOINT_HOST" in str(exc_info.value)


def test_the_expected_host_is_normalised_to_lowercase(monkeypatch):
    """R2 prints the account id in the console; an operator retyping it is one
    shift key away from a value that never matches a parsed hostname."""
    monkeypatch.delenv("S3_EXPECTED_ENDPOINT_HOST", raising=False)
    built = _production_settings(
        S3_EXPECTED_ENDPOINT_HOST="  OurOwnAccountId.R2.CloudflareStorage.com  "
    )
    assert built.S3_EXPECTED_ENDPOINT_HOST == _OUR_HOST

"""The S3 endpoint override, and the production rule that makes it acceptable.

BACKLOG `1.24`. `S3_ENDPOINT_URL` exists so the ingestion chain can be exercised
against a local S3-compatible process (E2E-2) without an AWS account. It is not
an ordinary setting: it redirects **every read and write of customer document
bytes**, so the test that matters most here is
`test_the_override_is_refused_in_production` — without it this module would be
documenting a redirect primitive rather than a development affordance.

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

from app.services import storage_service
from app.services.storage_service import StorageNotConfigured

_ENDPOINT = "http://127.0.0.1:9000"


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
    """A production process may not redirect customer documents off AWS.

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

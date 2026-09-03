"""Issue #144: every Redis connection takes its TLS posture from one seam.

Fourteen modules built Redis connection arguments. Thirteen wrote ``ssl.CERT_NONE``
for any ``rediss://`` URL and never read ``REDIS_TLS_INSECURE``, so the setting that
exists to keep verification on decided nothing, and a TLS broker on staging accepted
any certificate offered to it.

Two things are pinned here and one is pinned in pyproject.toml:

  * ``redis_ssl_kwargs`` itself, over the three inputs it can receive.
  * The three call sites that build their arguments inside a function, driven through
    that function with a ``rediss://`` URL and the flag off. Each asserts the
    arguments that reached ``from_url``, not the source that produced them.
  * The remaining eleven build their arguments at module import as a constant. The
    import-linter contract "ssl has one home" covers those: no module under ``app``
    except the seam may import ``ssl``, so none of them can name a verification mode.
    ``scripts/gates.py static`` runs it.

RED observed 2026-09-03 on the unfixed tree: the three call-site tests failed with
``ssl_cert_reqs`` 0 (CERT_NONE) against 2 (CERT_REQUIRED), and lint-imports reported
"ssl has one home" BROKEN naming all fourteen edges.
"""

from __future__ import annotations

import asyncio
import os
import ssl
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import app
from app.core.redis_tls import redis_ssl_kwargs

RE_DISS = "rediss://example.upstash.io:6380/0"
REDIS = "redis://localhost:6379/0"

# The eleven modules that build their ssl arguments once, at import, as a constant.
MODULE_LEVEL_SITES = (
    "app.worker.celery_app",
    "app.worker.tasks.pipeline.chunk",
    "app.worker.tasks.pipeline.embed",
    "app.worker.tasks.pipeline.metadata",
    "app.worker.tasks.pipeline.migrations",
    "app.worker.tasks.pipeline.parse",
    "app.worker.tasks.pipeline.provision",
    "app.worker.tasks.pipeline.strategy",
    "app.worker.tasks.runtime.agent",
    "app.worker.tasks.runtime.retrieve",
    "app.worker.tasks.runtime.validators",
)


# ---------------------------------------------------------------------------
# The seam
# ---------------------------------------------------------------------------


class TestRedisSslKwargs:
    """redis_ssl_kwargs over the three inputs it can receive."""

    def test_plain_redis_url_gets_no_ssl_arguments(self):
        """A redis:// URL has no TLS socket, so there is nothing to configure."""
        assert redis_ssl_kwargs(REDIS) == {}

    def test_rediss_url_verifies_by_default(self):
        """REDIS_TLS_INSECURE defaults to False, so the certificate and hostname are checked."""
        import app.core.redis_tls as seam  # noqa: PLC0415

        with patch.object(seam.settings, "REDIS_TLS_INSECURE", False, create=True):
            kwargs = redis_ssl_kwargs(RE_DISS)

        assert kwargs == {"ssl_cert_reqs": ssl.CERT_REQUIRED, "ssl_check_hostname": True}

    def test_rediss_url_relaxes_only_on_the_flag_and_warns(self):
        """REDIS_TLS_INSECURE=True is the one route to CERT_NONE, and it logs the exposure."""
        import app.core.redis_tls as seam  # noqa: PLC0415

        with (
            patch.object(seam.settings, "REDIS_TLS_INSECURE", True, create=True),
            patch.object(seam, "log") as mock_log,
        ):
            kwargs = redis_ssl_kwargs(RE_DISS)

        assert kwargs == {"ssl_cert_reqs": ssl.CERT_NONE}
        mock_log.warning.assert_called_once()
        event_name = mock_log.warning.call_args[0][0]
        assert "tls" in event_name.lower(), (
            f"Expected a TLS-related warning event, got {event_name!r}. "
            "Dropping verification must be visible in the logs of whatever opened the connection."
        )


# ---------------------------------------------------------------------------
# The call sites that build their arguments inside a function
# ---------------------------------------------------------------------------


class TestCallSitesVerifyByDefault:
    """Each factory, driven with a rediss:// URL and REDIS_TLS_INSECURE off."""

    def test_api_get_async_redis(self):
        """app/api/deps.py — the api's own Redis, behind SSE and rate limiting."""
        import app.api.deps as deps  # noqa: PLC0415

        captured: dict = {}

        def _fake_from_url(url, **kwargs):
            captured.update(kwargs)
            client = MagicMock()
            client.aclose = AsyncMock()
            return client

        async def _drain():
            with (
                patch.object(deps.aioredis.Redis, "from_url", side_effect=_fake_from_url),
                patch.object(deps.settings, "REDIS_URL", RE_DISS),
                patch.object(deps.settings, "REDIS_TLS_INSECURE", False, create=True),
            ):
                agen = deps.get_async_redis()
                await agen.__anext__()
                await agen.aclose()

        asyncio.run(_drain())

        assert captured.get("ssl_cert_reqs") == ssl.CERT_REQUIRED, (
            f"app/api/deps.py passed ssl_cert_reqs={captured.get('ssl_cert_reqs')!r}, "
            f"expected ssl.CERT_REQUIRED ({ssl.CERT_REQUIRED!r})."
        )
        assert captured.get("ssl_check_hostname") is True

    def test_api_url_query_cannot_downgrade_the_connection(self):
        """A ``?ssl_cert_reqs=none`` on REDIS_URL must never reach redis-py.

        On redis-py 6.4.0 ``parse_url`` forwards the query key as a string,
        ``from_url`` lets the URL options overwrite the keyword arguments, and
        ``RedisSSLContext`` maps ``"none"`` to ``ssl.CERT_NONE``. So the query would
        beat the seam's ``CERT_REQUIRED`` if the site did not strip it. This drives the
        real ``from_url`` and reads the pool the client was actually built with, rather
        than the arguments the site passed.
        """
        import app.api.deps as deps  # noqa: PLC0415

        dirty = f"{RE_DISS}?ssl_cert_reqs=none"
        pools: list = []

        async def _drain():
            with (
                patch.object(deps.settings, "REDIS_URL", dirty),
                patch.object(deps.settings, "REDIS_TLS_INSECURE", False, create=True),
            ):
                agen = deps.get_async_redis()
                client = await agen.__anext__()
                pools.append(client.connection_pool)
                await agen.aclose()

        asyncio.run(_drain())

        pool = pools[0]
        assert pool.connection_kwargs.get("ssl_cert_reqs") == ssl.CERT_REQUIRED, (
            f"redis-py was built with ssl_cert_reqs="
            f"{pool.connection_kwargs.get('ssl_cert_reqs')!r} from {dirty!r}. "
            "The query string has to be stripped before from_url sees the URL."
        )

        conn = pool.connection_class(**pool.connection_kwargs)
        assert conn.cert_reqs == ssl.CERT_REQUIRED
        assert conn.ssl_context.check_hostname is True

    def test_agent_tools_qembed_redis(self):
        """app/services/agent_tools.py — the query-embedding cache client."""
        import app.services.agent_tools as agent_tools  # noqa: PLC0415

        agent_tools._qembed_redis = None
        captured: dict = {}

        def _fake_from_url(url, **kwargs):
            captured.update(kwargs)
            return MagicMock()

        try:
            with (
                patch.object(agent_tools.redis_lib, "from_url", side_effect=_fake_from_url),
                patch.object(agent_tools.settings, "REDIS_URL", RE_DISS),
                patch.object(agent_tools.settings, "REDIS_TLS_INSECURE", False, create=True),
            ):
                agent_tools._get_qembed_redis()
        finally:
            agent_tools._qembed_redis = None

        assert captured.get("ssl_cert_reqs") == ssl.CERT_REQUIRED, (
            f"app/services/agent_tools.py passed ssl_cert_reqs={captured.get('ssl_cert_reqs')!r}, "
            f"expected ssl.CERT_REQUIRED ({ssl.CERT_REQUIRED!r})."
        )
        assert captured.get("ssl_check_hostname") is True

    def test_enforcement_rate_limit_redis(self):
        """app/services/transactional/enforcement.py — the one site that already read the flag."""
        import app.services.transactional.enforcement as enf  # noqa: PLC0415

        enf._rate_limit_redis = None
        captured: dict = {}

        def _fake_from_url(url, **kwargs):
            captured.update(kwargs)
            return MagicMock()

        try:
            with (
                patch.object(enf.redis_lib, "from_url", side_effect=_fake_from_url),
                patch.object(enf.settings, "REDIS_URL", RE_DISS),
                patch.object(enf.settings, "REDIS_TLS_INSECURE", False, create=True),
            ):
                enf._get_redis()
        finally:
            enf._rate_limit_redis = None

        assert captured.get("ssl_cert_reqs") == ssl.CERT_REQUIRED
        assert captured.get("ssl_check_hostname") is True


# ---------------------------------------------------------------------------
# Celery, the site the staging warning came from
# ---------------------------------------------------------------------------


class TestCeleryTakesTheSeamDict:
    """app/worker/celery_app.py passes the seam's dict as broker_use_ssl.

    Celery reads those two options through its own validation rather than handing
    them to redis-py untouched, so the seam's dict is driven through a real Celery
    app here instead of being compared to a literal.
    """

    def test_rediss_backend_gets_cert_required_and_an_ssl_connection(self):
        from celery import Celery  # noqa: PLC0415

        import app.core.redis_tls as seam  # noqa: PLC0415

        url = "rediss://:pw@example.upstash.io:6380/0"
        with patch.object(seam.settings, "REDIS_TLS_INSECURE", False, create=True):
            opts = redis_ssl_kwargs(url)

        app = Celery("test_redis_tls_seam")
        app.conf.update(
            broker_url=url,
            result_backend=url,
            broker_use_ssl=opts,
            redis_backend_use_ssl=opts,
        )

        connparams = app.backend.connparams
        assert connparams["ssl_cert_reqs"] == ssl.CERT_REQUIRED
        assert connparams["ssl_check_hostname"] is True
        assert connparams["connection_class"].__name__ == "SSLConnection"

    def test_plain_redis_backend_takes_no_ssl_options(self):
        """The seam hands a plain redis:// URL nothing, so no ssl option reaches the backend."""
        from celery import Celery  # noqa: PLC0415

        assert redis_ssl_kwargs(REDIS) == {}

        app = Celery("test_redis_tls_seam_plain")
        app.conf.update(broker_url=REDIS, result_backend=REDIS)

        connparams = app.backend.connparams
        assert "ssl_cert_reqs" not in connparams

    def test_rediss_broker_connection_verifies_the_certificate(self):
        """The broker side, which carries every task payload, not only the result backend.

        kombu builds the broker connection from ``broker_use_ssl`` and redis-py builds
        the result backend from ``redis_backend_use_ssl``. They are separate paths, so
        the broker gets its own assertion.
        """
        from celery import Celery  # noqa: PLC0415

        import app.core.redis_tls as seam  # noqa: PLC0415

        url = "rediss://:pw@example.upstash.io:6380/0"
        with patch.object(seam.settings, "REDIS_TLS_INSECURE", False, create=True):
            opts = redis_ssl_kwargs(url)

        app = Celery("test_redis_tls_seam_broker")
        app.conf.update(
            broker_url=url,
            result_backend=url,
            broker_use_ssl=opts,
            redis_backend_use_ssl=opts,
        )

        assert app.connection_for_write().ssl == {
            "ssl_cert_reqs": ssl.CERT_REQUIRED,
            "ssl_check_hostname": True,
        }

    def test_plain_redis_broker_takes_the_seam_dict_unconditionally(self):
        """The site passes the seam dict with no guard, and kombu ignores an empty one.

        kombu's redis transport reads the options under ``if conninfo.ssl:``
        (``kombu/transport/redis.py``, ``Channel._connparams``), so ``{}`` under a
        ``redis://`` scheme is skipped rather than rejected. This reads the app the
        worker actually boots, under the ``redis://`` URL ``tests/conftest.py`` pins.
        """
        from app.worker.celery_app import celery_app as booted  # noqa: PLC0415

        assert booted.conf.broker_url.startswith("redis://"), (
            f"Expected the pinned test REDIS_URL, got {booted.conf.broker_url!r}. "
            "tests/conftest.py sets redis://localhost:6379/1 before any app import."
        )
        assert booted.conf.broker_use_ssl == {}
        assert booted.conf.redis_backend_use_ssl == {}
        assert not booted.connection_for_write().ssl


# ---------------------------------------------------------------------------
# The eleven call sites that build their arguments at module import
# ---------------------------------------------------------------------------

_MODULE_LEVEL_PROBE = """
import importlib
import sys

from app.core.redis_tls import redis_ssl_kwargs

for name in sys.argv[1:]:
    importlib.import_module(name)

checked = []
bad = []
for name in sorted(sys.modules):
    mod = sys.modules[name]
    if not name.startswith("app.") or not hasattr(mod, "_ssl_opts"):
        continue
    url = getattr(mod, "_url_clean", None) or getattr(mod, "_redis_url_clean", None)
    if url is None:
        bad.append(name + ": _ssl_opts with no cleaned url to check it against")
        continue
    checked.append(name)
    want = redis_ssl_kwargs(url)
    got = mod._ssl_opts
    if got != want:
        bad.append(
            name + ": _ssl_opts=" + repr(got) + " but redis_ssl_kwargs(url)=" + repr(want)
        )

print("CHECKED " + " ".join(checked))
for line in bad:
    print("MISMATCH " + line)
sys.exit(1 if bad else 0)
"""


class TestModuleLevelSitesTakeTheSeamDict:
    """The eleven sites whose ssl arguments are a module constant, imported for real.

    The import-linter contract "ssl has one home" stops a site writing
    ``ssl.CERT_NONE``, because it cannot import ``ssl``. It does not stop the string
    spelling: redis-py 6.4.0 accepts ``ssl_cert_reqs="none"`` and maps it to
    ``ssl.CERT_NONE`` in ``RedisSSLContext``, so a hand-written
    ``{"ssl_cert_reqs": "none"}`` needs no import and passes the contract. Here each
    site's constant has to equal what ``redis_ssl_kwargs`` returns for that site's own
    cleaned URL, which no hand-written dict matches.

    A module constant is fixed at import, so this runs in a subprocess with
    ``REDIS_URL`` set to a ``rediss://`` URL and ``REDIS_TLS_INSECURE`` false.
    Reloading eleven modules in this process would rebind ``celery_app`` and
    re-register every task on the live app for the rest of the session.
    """

    def test_every_module_level_site_matches_the_seam(self):
        api_root = str(Path(app.__file__).resolve().parent.parent)
        env = {
            **os.environ,
            "REDIS_URL": RE_DISS,
            "REDIS_TLS_INSECURE": "False",
            "PYTHONPATH": api_root,
        }

        proc = subprocess.run(  # noqa: S603
            [sys.executable, "-c", _MODULE_LEVEL_PROBE, *MODULE_LEVEL_SITES],
            capture_output=True,
            text=True,
            cwd=api_root,
            env=env,
            timeout=600,
            check=False,
        )
        stdout = proc.stdout.strip()

        assert proc.returncode == 0, (
            "A module-level site built its ssl arguments itself instead of taking them "
            f"from redis_ssl_kwargs.\n{stdout}\n{proc.stderr[-2000:]}"
        )

        checked: list[str] = []
        for line in stdout.splitlines():
            if line.startswith("CHECKED "):
                checked = line.split()[1:]
        missing = sorted(set(MODULE_LEVEL_SITES) - set(checked))
        assert not missing, (
            f"These sites never reached the comparison: {missing}.\n{stdout}\n"
            f"{proc.stderr[-2000:]}"
        )

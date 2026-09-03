"""One place that decides the TLS posture of a Redis connection (issue #144).

WR-04 added ``REDIS_TLS_INSECURE`` so certificate verification stays on by default.
One call site read it. Thirteen others hardcoded ``ssl.CERT_NONE`` for any
``rediss://`` URL, so the setting decided nothing and a TLS broker on staging
accepted any certificate presented to it. Every Redis client in this service now
takes its ssl arguments from ``redis_ssl_kwargs`` below, and the import-linter
contract "ssl has one home" is what keeps it that way: no other module under
``app`` may import ``ssl``, so no other module can name a verification mode.
"""

import ssl
from functools import lru_cache

import structlog

from app.core.config import settings

log = structlog.get_logger(__name__)


@lru_cache(maxsize=None)
def _warn_tls_verification_disabled(url_prefix: str) -> None:
    """Log the relaxation once per process, keyed on the URL it applies to.

    ``app/api/deps.py::get_async_redis`` builds a client for every API request, so a
    warning on each call wrote a line per request into the API log. The cache is the
    once: a repeat for the same URL hits it and logs nothing. A test that asserts on
    the warning calls ``_warn_tls_verification_disabled.cache_clear()`` first, or uses
    a URL of its own.
    """
    log.warning(
        "redis.tls_verification_disabled",
        url_prefix=url_prefix,
        note=(
            "REDIS_TLS_INSECURE=True disables TLS certificate verification on every "
            "Redis connection this process opens to that URL (MITM exposure). Only "
            "acceptable for documented local/dev exceptions."
        ),
    )


def redis_ssl_kwargs(url: str) -> dict:
    """The ssl arguments a Redis connection to ``url`` should be opened with.

    Empty for a plain ``redis://`` URL, which has no TLS socket to configure.
    kombu reads Celery's two ssl options under ``if conninfo.ssl:``, so the empty
    dict is skipped there rather than rejected.

    For ``rediss://`` the default verifies the server certificate and checks the
    hostname against it. ``REDIS_TLS_INSECURE=True`` drops to ``ssl.CERT_NONE`` and
    warns once per process per URL, so the exposure appears in the logs of whichever
    process opened the connection without one line per API request.

    The dict this returns is accepted both as keyword arguments to
    ``redis.from_url`` / ``redis.asyncio.Redis.from_url`` and as Celery's
    ``broker_use_ssl`` and ``redis_backend_use_ssl`` values.

    Pass the URL with its query string already stripped. On redis-py 6.4.0
    ``parse_url`` forwards an ``ssl_cert_reqs`` query key as a string, ``from_url``
    lets the URL options overwrite the keyword arguments, and ``RedisSSLContext``
    maps the string ``"none"`` to ``ssl.CERT_NONE``. A URL carrying
    ``?ssl_cert_reqs=none`` therefore beats whatever this function returns, and
    stripping the query is what stops it.
    """
    if not url.startswith("rediss://"):
        return {}

    if settings.REDIS_TLS_INSECURE:
        _warn_tls_verification_disabled(url[:40])
        return {"ssl_cert_reqs": ssl.CERT_NONE}

    return {"ssl_cert_reqs": ssl.CERT_REQUIRED, "ssl_check_hostname": True}

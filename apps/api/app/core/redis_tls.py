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

import structlog

from app.core.config import settings

log = structlog.get_logger(__name__)


def redis_ssl_kwargs(url: str) -> dict:
    """The ssl arguments a Redis connection to ``url`` should be opened with.

    Empty for a plain ``redis://`` URL. redis-py has no TLS socket to configure
    there, and Celery raises ``ValueError`` when an ssl argument reaches a
    ``redis://`` scheme, so the empty dict is the correct answer rather than a
    shortcut.

    For ``rediss://`` the default verifies the server certificate and checks the
    hostname against it. ``REDIS_TLS_INSECURE=True`` drops to ``ssl.CERT_NONE``
    and logs a warning every time a connection is built, so the exposure appears
    in the logs of whichever process opened it.

    The dict this returns is accepted both as keyword arguments to
    ``redis.from_url`` / ``redis.asyncio.Redis.from_url`` and as Celery's
    ``broker_use_ssl`` and ``redis_backend_use_ssl`` values. Pass the URL with
    its query string already stripped: redis-py reads ``ssl_cert_reqs`` from
    keyword arguments only, never from the URL.
    """
    if not url.startswith("rediss://"):
        return {}

    if settings.REDIS_TLS_INSECURE:
        log.warning(
            "redis.tls_verification_disabled",
            url_prefix=url[:40],
            note=(
                "REDIS_TLS_INSECURE=True disables TLS certificate verification on this "
                "Redis connection (MITM exposure). Only acceptable for documented "
                "local/dev exceptions."
            ),
        )
        return {"ssl_cert_reqs": ssl.CERT_NONE}

    return {"ssl_cert_reqs": ssl.CERT_REQUIRED, "ssl_check_hostname": True}

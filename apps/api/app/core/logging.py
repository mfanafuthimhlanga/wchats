"""
Structured JSON logging via structlog.

configure_logging() — call once at application startup.
RequestIdMiddleware — pure ASGI middleware (not the Starlette base class) that
    binds a per-request UUID to structlog contextvars so every log line within
    a request automatically carries request_id.

Why pure ASGI:
    The Starlette base middleware class runs the downstream handler in a task
    group, which creates an isolated copy of the contextvars mapping.
    bind_contextvars() calls inside route handlers do not propagate back to the
    middleware layer.  The pure ASGI approach avoids this copy and preserves
    full context propagation. (Ref: github.com/tiangolo/fastapi/issues/4696)
"""

import logging
import uuid

import structlog
from starlette.types import ASGIApp, Receive, Scope, Send


def configure_logging(log_level: str = "INFO") -> None:
    """Configure structlog with a JSON renderer pipeline."""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            # Without this, exc_info=True renders as the literal `"exc_info":
            # true` and the traceback is dropped (#142).
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
    )
    logging.basicConfig(level=log_level.upper())


class RequestIdMiddleware:
    """
    Pure ASGI middleware that generates a UUID per HTTP request and binds it
    to structlog contextvars so all log lines within the request carry
    ``request_id``.

    Usage:
        app.add_middleware(RequestIdMiddleware)   # or
        app = RequestIdMiddleware(app)
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            structlog.contextvars.clear_contextvars()
            structlog.contextvars.bind_contextvars(request_id=str(uuid.uuid4()))
        await self.app(scope, receive, send)

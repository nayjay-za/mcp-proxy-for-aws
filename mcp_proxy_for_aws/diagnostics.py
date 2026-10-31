"""Bounded diagnostics that never inspect exception messages or tool payloads."""

import contextvars
import copy
import httpx
import logging
from mcp import McpError
from pathlib import Path
from typing import Any


reference_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    'proxy_reference_id', default=None
)


def exception_details(error: BaseException) -> dict[str, Any]:
    """Collect types, stack locations and numeric status codes, without values or locals."""
    chain: list[dict[str, Any]] = []
    seen: set[int] = set()
    pending = [error]
    http_status = None
    mcp_code = None
    category = 'internal'
    while pending and len(chain) < 16:
        current = pending.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        frames = []
        tb = current.__traceback__
        while tb is not None and len(frames) < 12:
            frames.append(
                {
                    'file': Path(tb.tb_frame.f_code.co_filename).name,
                    'function': tb.tb_frame.f_code.co_name,
                    'line': tb.tb_lineno,
                }
            )
            tb = tb.tb_next
        chain.append({'type': type(current).__name__, 'frames': frames})
        if isinstance(current, httpx.HTTPStatusError):
            http_status = current.response.status_code
        if isinstance(current, McpError):
            mcp_code = current.error.code
        if isinstance(current, (TimeoutError, httpx.TimeoutException)):
            category = 'timeout'
        elif (
            isinstance(current, (httpx.TransportError, ConnectionError)) and category == 'internal'
        ):
            category = 'transport'
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        elif current.__context__ is not None and not current.__suppress_context__:
            pending.append(current.__context__)
        # Support ExceptionGroup without increasing the Python 3.10 minimum.
        children = getattr(current, 'exceptions', ())
        if isinstance(children, tuple):
            pending.extend(x for x in children[:16] if isinstance(x, BaseException))
    if http_status is not None:
        category = {401: 'authentication', 403: 'authorization', 429: 'rate_limit'}.get(
            http_status, 'http'
        )
    elif mcp_code is not None:
        category = 'mcp'
    return {'category': category, 'http_status': http_status, 'mcp_code': mcp_code, 'chain': chain}


class SafeDiagnosticFilter(logging.Filter):
    """Allow only our diagnostic events; reduce dependency logs to safe metadata.

    This also covers background HTTP/session tasks where request ContextVars may be
    absent or stale. Never format the original record or exception, even at DEBUG.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Omit original messages, arguments, source lines, locals and cached tracebacks."""
        if not getattr(record, 'proxy_safe_diagnostic', False):
            details = (
                exception_details(record.exc_info[1])
                if record.exc_info and record.exc_info[1]
                else None
            )
            record.msg = 'proxy_event reference=%s details=%s'
            record.args = (reference_id.get(), details)
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


class SafeDiagnosticFormatter(logging.Formatter):
    """Format a sanitized copy so other logging handlers do not share mutations."""

    def format(self, record: logging.LogRecord) -> str:
        """Sanitize only the record emitted to the configured stderr handler."""
        safe_record = copy.copy(record)
        SafeDiagnosticFilter().filter(safe_record)
        return super().format(safe_record)

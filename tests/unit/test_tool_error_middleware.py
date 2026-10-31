"""Tests for correlated diagnostics without request or credential disclosure."""

import anyio
import httpx
import io
import logging
import mcp.types as mt
import pytest
import re
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import MiddlewareContext
from fastmcp.tools import ToolResult
from mcp import McpError
from mcp.types import ErrorData
from mcp_proxy_for_aws.diagnostics import SafeDiagnosticFilter, exception_details, reference_id
from mcp_proxy_for_aws.logging_config import configure_logging
from mcp_proxy_for_aws.middleware.tool_error_middleware import ToolErrorMiddleware
from unittest.mock import AsyncMock


def context():
    """Build a request with synthetic sensitive input to check non-disclosure."""
    return MiddlewareContext(
        message=mt.CallToolRequestParams(
            name='aws___run_script', arguments={'code': 'PRIVATE_CODE'}
        ),
        type='request',
        method='tools/call',
    )


@pytest.mark.asyncio
async def test_success_and_structured_errors_preserved(caplog):
    """Both success and upstream errors retain every result field unchanged."""
    for is_error in (False, True):
        result = ToolResult(
            content=[
                mt.TextContent(type='text', text='upstream detail'),
                mt.ImageContent(type='image', data='AA==', mimeType='image/png'),
            ],
            structured_content={'api_calls': [], 'code': 'UPSTREAM_ERROR'},
            meta={'upstream': 'metadata'},
            is_error=is_error,
        )
        assert (
            await ToolErrorMiddleware().on_call_tool(context(), AsyncMock(return_value=result))
            is result
        )
    assert 'upstream detail' not in caplog.text
    assert 'PRIVATE_CODE' not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'error,category,status',
    [
        (RuntimeError(), 'internal', None),
        (httpx.ConnectError('PRIVATE_TOKEN'), 'transport', None),
        (httpx.ReadTimeout('PRIVATE_TOKEN'), 'timeout', None),
        (
            McpError(
                ErrorData(code=-32602, message='PRIVATE_TOKEN', data={'secret': 'PRIVATE_TOKEN'})
            ),
            'mcp',
            None,
        ),
        *[
            (
                httpx.HTTPStatusError(
                    'PRIVATE_TOKEN',
                    request=httpx.Request(
                        'POST',
                        'https://example.invalid/PRIVATE_URL',
                        headers={'Authorization': 'PRIVATE_TOKEN'},
                    ),
                    response=httpx.Response(code),
                ),
                category,
                code,
            )
            for code, category in [
                (401, 'authentication'),
                (403, 'authorization'),
                (429, 'rate_limit'),
                (500, 'http'),
            ]
        ],
    ],
)
async def test_errors_have_safe_reference_and_type(error, category, status, caplog):
    """Empty, transport, auth and protocol errors carry safe diagnostic references."""
    with pytest.raises(ToolError) as caught:
        await ToolErrorMiddleware().on_call_tool(context(), AsyncMock(side_effect=error))
    message = str(caught.value)
    match = re.search(r'Reference: ([a-f0-9]{32})', message)
    assert match is not None
    assert match.group(1) in caplog.text
    assert category in message
    assert type(error).__name__ in message
    assert caught.value.__cause__ is error
    if status:
        assert f'HTTP {status}' in message
    if isinstance(error, McpError):
        assert 'MCP code -32602' in message
    assert 'PRIVATE_' not in message + caplog.text
    assert reference_id.get() is None


@pytest.mark.asyncio
async def test_timeout_is_bounded_and_named(caplog):
    """An empty TimeoutError carries its class, elapsed duration and reference."""

    async def hang(context: MiddlewareContext[mt.CallToolRequestParams]) -> ToolResult:
        await anyio.sleep_forever()
        return ToolResult(content=[])

    with pytest.raises(ToolError, match='timeout; TimeoutError'):
        await ToolErrorMiddleware(0.01).on_call_tool(context(), hang)
    assert 'elapsed_ms=' in caplog.text


def test_nested_causes_cycles_and_tracebacks():
    """Cause chains keep frame locations but omit messages, locals and source lines."""
    try:
        raise httpx.ConnectError('PRIVATE_TOKEN')
    except httpx.ConnectError as inner:
        outer = RuntimeError('PRIVATE_PAYLOAD')
        outer.__cause__ = inner
        inner.__context__ = outer
        details = exception_details(outer)
    assert [x['type'] for x in details['chain']] == ['RuntimeError', 'ConnectError']
    assert (
        details['chain'][1]['frames'][0]['function'] == 'test_nested_causes_cycles_and_tracebacks'
    )
    assert 'PRIVATE_' not in str(details)
    assert details['category'] == 'transport'


def test_deep_chain_is_bounded():
    """Malformed or exceptionally long chains cannot grow diagnostics without bound."""
    error = RuntimeError()
    for _ in range(100):
        outer = RuntimeError()
        outer.__cause__ = error
        error = outer
    assert len(exception_details(error)['chain']) == 16


@pytest.mark.parametrize('with_reference', [True, False])
def test_dependency_logs_strip_all_payload_fields(with_reference):
    """Background tasks and request tasks both omit arbitrary messages and trace text."""
    token = reference_id.set('test-reference' if with_reference else None)
    try:
        try:
            raise RuntimeError('PRIVATE_TOKEN')
        except RuntimeError as error:
            record = logging.LogRecord(
                'fastmcp.server',
                logging.ERROR,
                __file__,
                1,
                'PRIVATE_PAYLOAD %s',
                ('PRIVATE_ARG',),
                (type(error), error, error.__traceback__),
            )
            record.exc_text = 'PRIVATE_CACHED_EXCEPTION'
            record.stack_info = 'PRIVATE_STACK'
        assert SafeDiagnosticFilter().filter(record)
        text = logging.Formatter('%(message)s').format(record)
        assert 'RuntimeError' in text
        assert 'PRIVATE_' not in text
    finally:
        reference_id.reset(token)


@pytest.mark.asyncio
async def test_real_protocol_failure_and_log_redaction():
    """Exercise FastMCP's own wrapper through the normal protocol, without AWS."""
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    output = io.StringIO()
    try:
        configure_logging('DEBUG')
        handler = root.handlers[0]
        assert isinstance(handler, logging.StreamHandler)
        handler.setStream(output)
        server = FastMCP('local-test')
        server.add_middleware(ToolErrorMiddleware())

        @server.tool()
        async def failure() -> str:
            """Raise a controlled local failure without AWS or networking."""
            logging.getLogger('dependency').error('Authorization PRIVATE_TOKEN')
            raise RuntimeError('PRIVATE_TOKEN')

        with anyio.fail_after(10):
            async with Client(server) as client:
                result = await client.call_tool('failure', {}, raise_on_error=False)
        assert result.is_error
        assert isinstance(result.content[0], mt.TextContent)
        assert 'Reference:' in result.content[0].text
        assert 'RuntimeError' in output.getvalue()
        assert 'PRIVATE_TOKEN' not in output.getvalue() + str(result)
    finally:
        root.handlers = handlers
        root.setLevel(level)


@pytest.mark.asyncio
async def test_parallel_requests_have_distinct_references():
    """Concurrent failures keep separate references and restore their parent context."""
    import asyncio

    async def fail():
        try:
            await ToolErrorMiddleware().on_call_tool(
                context(), AsyncMock(side_effect=RuntimeError())
            )
        except ToolError as error:
            match = re.search(r'Reference: ([a-f0-9]{32})', str(error))
            assert match is not None
            return match.group(1)

    references = await asyncio.gather(fail(), fail())
    assert references[0] != references[1]
    assert reference_id.get() is None

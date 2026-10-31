# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import anyio
import logging
import mcp.types as mt
import time
import uuid
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import ToolResult
from mcp_proxy_for_aws.diagnostics import exception_details, reference_id


logger = logging.getLogger(__name__)


class ToolErrorMiddleware(Middleware):
    """Bound tool calls and provide correlated, payload-free failure diagnostics."""

    def __init__(self, tool_call_timeout: float = 300.0) -> None:
        """Set the maximum call duration in seconds."""
        super().__init__()
        self._tool_call_timeout = tool_call_timeout

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        """Preserve upstream results and report safe details for raised exceptions."""
        ref = uuid.uuid4().hex
        token = reference_id.set(ref)
        started = time.monotonic()
        safe = {'proxy_safe_diagnostic': True}
        try:
            logger.info('tool_start reference=%s', ref, extra=safe)
            with anyio.fail_after(self._tool_call_timeout):
                result = await call_next(context)
            if result.is_error:
                logger.error(
                    'tool_upstream_error reference=%s elapsed_ms=%d',
                    ref,
                    int((time.monotonic() - started) * 1000),
                    extra=safe,
                )
            else:
                logger.info(
                    'tool_success reference=%s elapsed_ms=%d',
                    ref,
                    int((time.monotonic() - started) * 1000),
                    extra=safe,
                )
            # Preserve content, structuredContent, meta and isError unchanged.
            return result
        except Exception as error:
            details = exception_details(error)
            logger.error(
                'tool_exception reference=%s elapsed_ms=%d details=%s',
                ref,
                int((time.monotonic() - started) * 1000),
                details,
                extra=safe,
            )
            status = ''
            if details['http_status'] is not None:
                status += f' HTTP {details["http_status"]}.'
            if details['mcp_code'] is not None:
                status += f' MCP code {details["mcp_code"]}.'
            # Exception messages can contain code, bodies, URLs or secrets.
            raise ToolError(
                f'AWS MCP proxy request failed ({details["category"]}; '
                f'{details["chain"][0]["type"]}).{status} Reference: {ref}. '
                'Consult proxy diagnostics before deciding whether to retry.'
            ) from error
        finally:
            reference_id.reset(token)

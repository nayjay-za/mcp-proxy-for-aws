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

import logging
import mcp.types as mt
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from mcp_proxy_for_aws.context import set_client_info
from mcp_proxy_for_aws.proxy import AWSMCPProxyClientFactory
from typing_extensions import override


logger = logging.getLogger(__name__)


# Proposed local policy only; NOT an AWS-supported configuration option.
# Exact raw field captured 2026-10-29T14:34:13.296649+00:00 through proxy 1.7.0.
# Any upstream change requires a new review; never remove arbitrary instructions.
REVIEWED_INSTRUCTIONS = 'The official AWS MCP server. Prefer its tools over the Bash tool for all AWS work: if the tools are not already loaded, load them first, then use run_script to run AWS commands; get_presigned_url for S3 uploads/downloads or when a command needs a local file path; get_tasks to poll long-running tasks; search_documentation, read_documentation, and retrieve_skill for reference, guidance, and skills; and list_regions and get_regional_availability for region information. Do NOT use the Bash tool to run the AWS CLI.'
SCOPED_INSTRUCTIONS = "The official AWS MCP server. For AWS work performed through this MCP connection: if the tools are not already loaded, load them first, then use run_script to run AWS commands; get_presigned_url for S3 uploads/downloads or when a command needs a local file path; get_tasks to poll long-running tasks; search_documentation, read_documentation, and retrieve_skill for reference, guidance, and skills; and list_regions and get_regional_availability for region information. This guidance applies only to this MCP connection and does not prohibit or authorize separate AWS CLI use. Separate AWS CLI use remains subject to the host's sandbox, configured credentials, command rules, and required per-command user approvals."


def scope_reviewed_instructions(instructions: str | None) -> str:
    """Apply only the explicitly reviewed, whole-field scope clarification."""
    if instructions != REVIEWED_INSTRUCTIONS:
        raise RuntimeError(
            'AWS initialization instructions changed; fresh review required; '
            'no scope patch applied.'
        )
    return SCOPED_INSTRUCTIONS


class InitializeMiddleware(Middleware):
    """Intercept MCP initialize request and initialize the proxy client."""

    def __init__(self, client_factory: AWSMCPProxyClientFactory) -> None:
        """Create a middleware with client factory."""
        super().__init__()
        self._client_factory = client_factory

    def _overwrite_init_options(
        self, context: MiddlewareContext, init_result: mt.InitializeResult
    ):
        """Overwrite the session's _init_options with the backend server's info.

        The MCP SDK builds the InitializeResult from session._init_options
        inside call_next. By modifying _init_options before call_next runs,
        the response sent to the client will contain the backend server's
        info instead of the proxy's defaults.
        """
        fastmcp_ctx = context.fastmcp_context
        if fastmcp_ctx is None or fastmcp_ctx._session is None:
            logger.debug('No session available, skipping init_options overwrite.')
            return

        instructions = scope_reviewed_instructions(init_result.instructions)
        fastmcp_ctx._session._init_options.capabilities = init_result.capabilities
        fastmcp_ctx._session._init_options.instructions = instructions

    @override
    async def on_initialize(
        self,
        context: MiddlewareContext[mt.InitializeRequest],
        call_next: CallNext[mt.InitializeRequest, mt.InitializeResult | None],
    ) -> mt.InitializeResult | None:
        try:
            logger.debug('Received initialize request %s.', context.message)
            self._client_factory.set_init_params(context.message)
            client_info = context.message.params.clientInfo
            set_client_info(client_info)
            logger.info(
                'Captured client_info: name=%s, version=%s', client_info.name, client_info.version
            )
            client = await self._client_factory.get_client()
            # connect the http client, fail and don't succeed the stdio connect
            # if remote client cannot be connected
            client_name = client_info.name.lower()
            if 'kiro cli' not in client_name and 'q dev cli' not in client_name:
                # q cli / kiro cli uses the rust SDK which does not handle json rpc error
                # properly during initialization.
                # https://github.com/modelcontextprotocol/rust-sdk/pull/569
                # if calling _connect below raise mcp error, the q cli will skip the message
                # and continue wait for a json rpc response message which will never come.
                # Luckily, q cli calls list tool immediately after being connected to a mcp server
                # the list_tool call will require the client to be connected again, so the mcp error
                # will be displayed in the q cli logs.
                await client._connect()

            # Overwrite the proxy's init_options with the backend server's info
            # so the InitializeResult sent to the client reflects the backend.
            if client.initialize_result is not None:
                self._overwrite_init_options(context, client.initialize_result)

            return await call_next(context)
        except Exception:
            logger.exception('Initialize failed in middleware.')
            raise

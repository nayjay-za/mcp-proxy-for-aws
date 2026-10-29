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

import ast
import copy
import hashlib
import inspect
import json
import mcp.types as mt
import pytest
from mcp_proxy_for_aws.middleware.initialize_middleware import (
    REVIEWED_INSTRUCTIONS,
    SCOPED_INSTRUCTIONS,
    InitializeMiddleware,
    scope_reviewed_instructions,
)
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock


def create_initialize_request(client_name: str) -> mt.InitializeRequest:
    """Create a real InitializeRequest object."""
    return mt.InitializeRequest(
        method='initialize',
        params=mt.InitializeRequestParams(
            protocolVersion='2024-11-05',
            capabilities=mt.ClientCapabilities(),
            clientInfo=mt.Implementation(name=client_name, version='1.0'),
        ),
    )


@pytest.mark.asyncio
async def test_on_initialize_connects_client():
    """Test that on_initialize calls client._connect()."""
    mock_client = Mock()
    mock_client._connect = AsyncMock()
    mock_client.initialize_result = mt.InitializeResult(
        protocolVersion='2024-11-05',
        capabilities=mt.ServerCapabilities(),
        serverInfo=mt.Implementation(name='backend-server', version='2.0'),
        instructions=REVIEWED_INSTRUCTIONS,
    )

    mock_factory = Mock()
    mock_factory.set_init_params = Mock()
    mock_factory.get_client = AsyncMock(return_value=mock_client)

    middleware = InitializeMiddleware(mock_factory)

    mock_init_options = Mock()
    mock_init_options.instructions = 'proxy instructions'
    mock_session = Mock()
    mock_session._init_options = mock_init_options
    mock_fastmcp_ctx = Mock()
    mock_fastmcp_ctx._session = mock_session

    mock_context = Mock()
    mock_context.message = create_initialize_request('test-client')
    mock_context.fastmcp_context = mock_fastmcp_ctx

    mock_call_next = AsyncMock()

    await middleware.on_initialize(mock_context, mock_call_next)

    mock_factory.set_init_params.assert_called_once_with(mock_context.message)
    mock_factory.get_client.assert_called_once()
    mock_client._connect.assert_called_once()
    mock_call_next.assert_called_once_with(mock_context)

    # Verify init_options capabilities were overwritten with backend server info
    assert mock_init_options.capabilities == mt.ServerCapabilities()
    assert mock_init_options.instructions == SCOPED_INSTRUCTIONS


@pytest.mark.asyncio
async def test_on_initialize_fails_if_connect_fails():
    """Test that on_initialize raises exception if _connect() fails."""
    mock_client = Mock()
    mock_client._connect = AsyncMock(side_effect=Exception('Connection failed'))

    mock_factory = Mock()
    mock_factory.set_init_params = Mock()
    mock_factory.get_client = AsyncMock(return_value=mock_client)

    middleware = InitializeMiddleware(mock_factory)

    mock_context = Mock()
    mock_context.message = create_initialize_request('test-client')

    mock_call_next = AsyncMock()

    with pytest.raises(Exception, match='Connection failed'):
        await middleware.on_initialize(mock_context, mock_call_next)

    mock_call_next.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'client_name',
    [
        'Kiro CLI',
        'kiro cli',
        'KIRO CLI',
        'Amazon Q Dev CLI',
        'amazon q dev cli',
        'Q DEV CLI',
    ],
)
async def test_on_initialize_skips_connect_for_special_clients(client_name):
    """Test that on_initialize skips _connect() for Kiro CLI and Q Dev CLI."""
    mock_client = Mock()
    mock_client._connect = AsyncMock()
    mock_client.initialize_result = None

    mock_factory = Mock()
    mock_factory.set_init_params = Mock()
    mock_factory.get_client = AsyncMock(return_value=mock_client)

    middleware = InitializeMiddleware(mock_factory)

    mock_context = Mock()
    mock_context.message = create_initialize_request(client_name)

    mock_call_next = AsyncMock()

    await middleware.on_initialize(mock_context, mock_call_next)

    mock_client._connect.assert_not_called()
    mock_call_next.assert_called_once_with(mock_context)


@pytest.mark.asyncio
async def test_on_initialize_overwrites_init_options_with_backend_info():
    """Test that on_initialize overwrites session init_options with backend server info."""
    backend_capabilities = mt.ServerCapabilities(
        logging=mt.LoggingCapability(),
    )
    backend_result = mt.InitializeResult(
        protocolVersion='2024-11-05',
        capabilities=backend_capabilities,
        serverInfo=mt.Implementation(name='backend-mcp', version='3.1'),
        instructions=REVIEWED_INSTRUCTIONS,
    )

    mock_client = Mock()
    mock_client._connect = AsyncMock()
    mock_client.initialize_result = backend_result

    mock_factory = Mock()
    mock_factory.set_init_params = Mock()
    mock_factory.get_client = AsyncMock(return_value=mock_client)

    middleware = InitializeMiddleware(mock_factory)

    mock_init_options = Mock()
    mock_init_options.server_name = 'proxy-name'
    mock_init_options.server_version = '1.0'
    mock_init_options.capabilities = mt.ServerCapabilities()
    mock_init_options.instructions = 'proxy instructions'
    mock_session = Mock()
    mock_session._init_options = mock_init_options
    mock_fastmcp_ctx = Mock()
    mock_fastmcp_ctx._session = mock_session

    mock_context = Mock()
    mock_context.message = create_initialize_request('test-client')
    mock_context.fastmcp_context = mock_fastmcp_ctx

    mock_call_next = AsyncMock()

    await middleware.on_initialize(mock_context, mock_call_next)

    assert mock_init_options.capabilities == backend_capabilities
    assert mock_init_options.instructions == SCOPED_INSTRUCTIONS


@pytest.mark.asyncio
async def test_on_initialize_disables_prompts_and_resources():
    """Test that prompts and resources capabilities are disabled even if backend supports them."""
    backend_capabilities = mt.ServerCapabilities(
        tools=mt.ToolsCapability(),
        prompts=mt.PromptsCapability(),
        resources=mt.ResourcesCapability(),
    )
    backend_result = mt.InitializeResult(
        protocolVersion='2024-11-05',
        capabilities=backend_capabilities,
        serverInfo=mt.Implementation(name='backend', version='1.0'),
        instructions=REVIEWED_INSTRUCTIONS,
    )

    mock_client = Mock()
    mock_client._connect = AsyncMock()
    mock_client.initialize_result = backend_result

    mock_factory = Mock()
    mock_factory.set_init_params = Mock()
    mock_factory.get_client = AsyncMock(return_value=mock_client)

    middleware = InitializeMiddleware(mock_factory)

    mock_init_options = Mock()
    mock_session = Mock()
    mock_session._init_options = mock_init_options
    mock_fastmcp_ctx = Mock()
    mock_fastmcp_ctx._session = mock_session

    mock_context = Mock()
    mock_context.message = create_initialize_request('test-client')
    mock_context.fastmcp_context = mock_fastmcp_ctx

    mock_call_next = AsyncMock()

    await middleware.on_initialize(mock_context, mock_call_next)

    assert mock_init_options.capabilities.prompts is not None
    assert mock_init_options.capabilities.resources is not None
    assert mock_init_options.capabilities.tools is not None


@pytest.mark.asyncio
async def test_on_initialize_skips_overwrite_when_no_session():
    """Test that overwrite is skipped when no session is available."""
    mock_client = Mock()
    mock_client._connect = AsyncMock()
    mock_client.initialize_result = mt.InitializeResult(
        protocolVersion='2024-11-05',
        capabilities=mt.ServerCapabilities(),
        serverInfo=mt.Implementation(name='backend', version='1.0'),
        instructions=REVIEWED_INSTRUCTIONS,
    )

    mock_factory = Mock()
    mock_factory.set_init_params = Mock()
    mock_factory.get_client = AsyncMock(return_value=mock_client)

    middleware = InitializeMiddleware(mock_factory)

    mock_context = Mock()
    mock_context.message = create_initialize_request('test-client')
    mock_context.fastmcp_context = None

    mock_call_next = AsyncMock()

    # Should not raise, just skip overwrite
    await middleware.on_initialize(mock_context, mock_call_next)
    mock_call_next.assert_called_once_with(mock_context)


def test_captured_full_field_accepted():
    """Accept the complete reviewed field without normalization."""
    assert scope_reviewed_instructions(REVIEWED_INSTRUCTIONS) == SCOPED_INSTRUCTIONS
    assert not REVIEWED_INSTRUCTIONS.startswith('\n')
    assert not REVIEWED_INSTRUCTIONS.endswith('\n')


@pytest.mark.parametrize(
    'text',
    [
        None,
        '',
        REVIEWED_INSTRUCTIONS + '\n',
        ' ' + REVIEWED_INSTRUCTIONS,
        REVIEWED_INSTRUCTIONS.lower(),
        REVIEWED_INSTRUCTIONS + ' More rules.',
        SCOPED_INSTRUCTIONS,
    ],
)
def test_any_unreviewed_field_rejected(text):
    """Reject absent, reformatted, extended, and already scoped fields."""
    with pytest.raises(RuntimeError, match='fresh review required'):
        scope_reviewed_instructions(text)


def test_only_two_reviewed_text_spans_changed():
    """Preserve all guidance except the two explicitly reviewed scope spans."""
    expected = REVIEWED_INSTRUCTIONS.replace(
        'Prefer its tools over the Bash tool for all AWS work:',
        'For AWS work performed through this MCP connection:',
    ).replace(
        'Do NOT use the Bash tool to run the AWS CLI.',
        'This guidance applies only to this MCP connection and does not prohibit or '
        'authorize separate AWS CLI use. Separate AWS CLI use remains subject to '
        "the host's sandbox, configured credentials, command rules, and required "
        'per-command user approvals.',
    )
    assert SCOPED_INSTRUCTIONS == expected


def overwrite_fixture(text=REVIEWED_INSTRUCTIONS):
    """Build synthetic session state and a real backend initialization result."""
    options = SimpleNamespace(
        instructions='original local',
        capabilities='old',
        server_name='unchanged',
        server_version='1.7.0',
    )
    context = Mock()
    context.fastmcp_context._session._init_options = options
    result = mt.InitializeResult(
        protocolVersion='2025-03-26',
        capabilities=mt.ServerCapabilities(tools=mt.ToolsCapability(listChanged=True)),
        serverInfo=mt.Implementation(name='backend', version='1.0'),
        instructions=text,
    )
    return context, result, options


def test_session_only_changes_instructions_and_existing_capability_forward():
    """Preserve backend metadata and session identity while forwarding capabilities."""
    context, result, options = overwrite_fixture()
    before_result = result.model_dump()
    InitializeMiddleware(Mock())._overwrite_init_options(context, result)
    assert options.instructions == SCOPED_INSTRUCTIONS
    assert options.capabilities is result.capabilities
    assert options.server_name == 'unchanged'
    assert options.server_version == '1.7.0'
    assert result.model_dump() == before_result


@pytest.mark.parametrize('text', [None, REVIEWED_INSTRUCTIONS + ' Updated rule.'])
async def test_drift_fails_before_session_mutation_or_call_next(text):
    """Fail initialization before mutating the session or sending a success response."""
    context, result, options = overwrite_fixture(text)
    before = copy.deepcopy(vars(options))
    context.message = create_initialize_request('test-client')
    client = Mock(initialize_result=result, _connect=AsyncMock())
    factory = Mock(get_client=AsyncMock(return_value=client))
    call_next = AsyncMock()
    with pytest.raises(RuntimeError, match='fresh review required'):
        await InitializeMiddleware(factory).on_initialize(context, call_next)
    assert vars(options) == before
    call_next.assert_not_called()


@pytest.mark.parametrize('fastmcp_context', [None, Mock(_session=None)])
def test_existing_no_session_behavior_unchanged(fastmcp_context):
    """Keep the original no-session early return even for absent instructions."""
    context = Mock(fastmcp_context=fastmcp_context)
    InitializeMiddleware(Mock())._overwrite_init_options(context, Mock(instructions=None))


def test_other_middleware_code_matches_reviewed_baseline():
    """Keep all unrelated code identical to v1.7.0, ignoring formatting and comments."""
    import mcp_proxy_for_aws.middleware.initialize_middleware as module

    tree = ast.parse(inspect.getsource(module))
    tree.body = [
        node
        for node in tree.body
        if not (
            isinstance(node, ast.FunctionDef)
            and node.name == 'scope_reviewed_instructions'
            or isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name)
                and target.id in {'REVIEWED_INSTRUCTIONS', 'SCOPED_INSTRUCTIONS'}
                for target in node.targets
            )
        )
    ]
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == 'InitializeMiddleware':
            node.body = [
                method
                for method in node.body
                if getattr(method, 'name', None) != '_overwrite_init_options'
            ]

    def canonical(node):
        if isinstance(node, ast.AST):
            return [
                type(node).__name__,
                {
                    field: canonical(value)
                    for field, value in ast.iter_fields(node)
                    if field != 'type_params'
                },
            ]
        if isinstance(node, list):
            return [canonical(value) for value in node]
        return node

    digest = hashlib.sha256(json.dumps(canonical(tree), sort_keys=True).encode()).hexdigest()
    assert digest == '580d945bf0d801e14f1d6de04f09001c33d8271d555e0ed866bb83f8315cf183'

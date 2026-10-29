# Personal initialization scope variant

This personal fork is based on AWS `v1.7.0`, commit
`22f1b3f8b7c4ef37112114b4af3d6c5a6c0a83bb`. It is a custom variant,
not an AWS-endorsed configuration option or a universal proxy for arbitrary MCP
endpoints. Before merging the variant into this fork's main branch, upstream
changes through `ddf5dbb181d9c3b499e06375cca0f3b2a60e30dc` were integrated and
the combined source was validated. That integration updates dependencies and
tool-lookup error reporting; the initialization middleware still matches the
reviewed v1.7.0 baseline apart from this patch. The upstream Apache-2.0 license,
copyright, and NOTICE remain intact.

## Scope and rationale

The official backend supplies its blanket AWS CLI prohibition in
`initialize.result.instructions`, not in the eight raw tool descriptions.
Clients can append server instructions to every tool's displayed description.
Changing descriptions would therefore target the wrong source of the text.

Only `InitializeMiddleware._overwrite_init_options` changes behavior. It compares
the entire backend instruction field with `REVIEWED_INSTRUCTIONS` and replaces it
with `SCOPED_INSTRUCTIONS`. The two reviewed spans scope guidance to this MCP
connection and explicitly preserve separate AWS CLI credentials, host sandbox,
command rules, and required per-command approvals. This neither authorizes nor
prohibits independent CLI use.

All other guidance, tool descriptions, schemas, annotations, capability forwarding,
profiles, credentials, and IAM remain unchanged. Missing, empty, reformatted, or
otherwise changed instruction text raises an error before session options change
or the initialization response is sent. No arbitrary text is stripped or filtered.
The existing no-session early return and special-client connection behavior remain
unchanged; this patch adds no validation where upstream has no initialization
result. Do not treat it as a general policy enforcement layer.

This deliberately trades availability for review: a backend wording update can
stop fresh initialization until the complete new field is reviewed. The captured
field's provenance is recorded beside the constants; it is not fetched during
build or tests. No live handshakes are needed for local verification.

## Install the actual source with locked dependencies

Use a reviewed full 40-character commit SHA from this fork, never a moving branch
or just the distribution version. The package retains version `1.7.0`, so version
output alone cannot identify this variant. Requirements: Git and uv `0.9.11`
(the version used to verify this workflow). Python is selected by `.python-version`.
All environments below are local to the checkout; do not point live launchers at
it as part of this procedure.

```sh
git clone https://github.com/nayjay-za/mcp-proxy-for-aws.git personal-aws-proxy
cd personal-aws-proxy
git checkout --detach <reviewed-full-commit-sha>
git rev-parse HEAD
uv sync --frozen --dev --no-install-project
uv sync --frozen --dev --no-editable --no-build-isolation
.venv/bin/python -I - <<'PYTHON'
from pathlib import Path
import mcp_proxy_for_aws.middleware.initialize_middleware as module
assert hasattr(module, "SCOPED_INSTRUCTIONS")
assert module.scope_reviewed_instructions(module.REVIEWED_INSTRUCTIONS) == module.SCOPED_INSTRUCTIONS
assert Path(module.__file__).resolve().is_relative_to(Path('.venv').resolve())
print(module.__file__)
PYTHON
.venv/bin/mcp-proxy-for-aws --help
```

`uv sync --frozen --dev --no-install-project` first installs the locked build
backend and development tools without building the root package. The second sync
builds and installs the **root source project** without build isolation, using that
locked backend. Runtime and development dependencies come from the committed
`uv.lock`. Use its
`.venv/bin/mcp-proxy-for-aws` entry point after a separately reviewed activation.
Do not install `packages/proxy`, `mcp-proxy-for-aws-cli==1.7.0`, or a registry-only
`uvx` command as a substitute: that metadata wrapper pins the published official
code distribution and can silently select unpatched upstream code.

For an offline build using the lockfile's build backend:

```sh
uv sync --frozen --dev --no-install-project
uv build --no-build-isolation
```

The wheel and sdist in `dist/` contain this fork's source. Their ordinary package
dependency metadata does not encode the lockfile: installing a wheel alone does
not reproduce locked dependencies. Keep the source commit and `uv.lock` together.
For immutable source installation, prefer the checkout procedure above.

## Verify and maintain

For local validation, use only mock tests. The integration directory targets
remote servers and is excluded, even when tests lack a `live` marker.

```sh
uv sync --frozen --dev
AWS_ACCESS_KEY_ID=testing AWS_SECRET_ACCESS_KEY=testing AWS_EC2_METADATA_DISABLED=true \
  AWS_CONFIG_FILE=/dev/null AWS_SHARED_CREDENTIALS_FILE=/dev/null \
  uv run --frozen pytest tests/unit packages/proxy/tests --cov --cov-branch
uv run --frozen coverage report --fail-under=80
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen --all-packages pyright
uv run --frozen pre-commit run --all-files
uv build --no-build-isolation
```

The regression suite checks whole-field acceptance/rejection, exactly two text
replacements, unchanged backend metadata and session identity, no partial mutation
on drift, no success response on drift, no-session behavior, and an AST fingerprint
of unrelated middleware code from the reviewed release. Review any fingerprint
update rather than mechanically accepting it. The upstream wrapper build can be
checked with `uv build packages/proxy -o dist --no-build-isolation`; it is not the
installation target for this fork.

For an upstream update:

1. Fetch `upstream` and verify the target tag and commit. Create a new branch from
   that immutable release, keeping this reviewed branch available.
2. Review all upstream changes to initialization, middleware, dependencies, and
   packaging before cherry-picking this patch (or rebasing a separate review branch).
3. Review the complete backend field through a separately authorized inspection.
   Never loosen the equality check, normalize text, or introduce generic filtering.
4. Resolve conflicts explicitly, update fixtures and the AST fingerprint only after
   reviewing the new baseline, and rerun the mock suite, quality checks, source import
   check, and build. Inspect the diff for secrets and account-specific configuration.
5. Commit and push a new reviewed branch without force-pushing, merging, or publishing
   a release. Record its full SHA. Any live migration needs a separate review.

To roll back source, create a fresh checkout at a previous reviewed full commit and
repeat the frozen source install there. Keep its lockfile. Do not overwrite a dirty
checkout or assume a source checkout changes an already running MCP process.
Changing live launcher configuration or activating/rolling back a runtime is a
separate operation, never a side effect of this source workflow.

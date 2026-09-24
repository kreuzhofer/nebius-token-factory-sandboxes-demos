# Python examples for Nebius Token Factory Sandboxes

Each example owns its launch command, agents, dependencies, documentation, and
tests. The examples share an adapter for the official Contree SDK and client
for Nebius Token Factory Sandboxes, plus local configuration loading.

| Example | Run from this directory | Guide |
| --- | --- | --- |
| Receipt expense reporting | `python3 -m receipt_demo --profile minimal` | [Receipt demo](receipt_demo/README.md) |
| Sandbox smoke and Python tool | `python3 -m basic_demo smoke` / `python3 -m basic_demo agent` | [Basic demo](basic_demo/README.md) |
| OpenCode coding agent | `python3 -m codingagent_demo --help` | [Coding demo](codingagent_demo/README.md) |
| Data analysis and checkpoint follow-ups | `python3 -m analysis_demo --output analysis-output` | [Analysis demo](analysis_demo/README.md) |
| Dependency upgrade and rollback | `python3 -m upgrade_demo --output upgrade-output` | [Upgrade demo](upgrade_demo/README.md) |

## Setup

From the repository root:

```sh
cd examples/python
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 -m basic_demo configure
python3 -m basic_demo smoke
```

Use Python 3.10–3.14 for local commands (the pinned SDK requires Python <3.15).
Install the shared SDK dependencies locally; receipt workers install those same
pins alongside their agent dependencies inside the sandboxes. Configuration lives in
`examples/python/.env`. Shell environment values take precedence. See the
[basic](basic_demo/.env.example), [receipt](receipt_demo/.env.example), and
[coding](codingagent_demo/.env.example) configuration examples for the settings
used by each workflow.

The analysis launcher also needs Pillow locally to validate returned charts:

```sh
python3 -m pip install -r analysis_demo/requirements.txt
```

Each guide documents its input format, additional dependencies, output files,
and failure behavior. Use a fresh output directory for every run.

## Layout

```text
nebius_sandbox.py       Sandbox images, files, commands, operations, and execution results
sandbox_events.py      Shared operation events, replay, reconnects and polling fallback
http_transport.py      Inference model discovery HTTPS transport and shared error type
configuration.py       Local settings and sandbox credentials
tests/                 Shared client and configuration tests
receipt_demo/          Receipt launcher, workers, inference settings, reports, and tests
basic_demo/            Smoke/tool launcher, sandbox agent, and tests
codingagent_demo/      Reusable OpenCode image, task helper, compatibility proof, and tests
analysis_demo/         CSV analysis, checkpoint follow-up, explicit context, and validation
upgrade_demo/          Dependency upgrade, trusted checks, independent branches, and rollback
pyproject.toml         Python lint/format rules
requirements.txt       Shared sandbox SDK/client dependency pins
requirements-dev.txt   Development dependencies across Python examples
```

The sandbox client knows no receipt paths or agent roles. Each example owns its
packaging and orchestration.

The client owns image import completion (`import_image_and_wait`) and validation
of retained filesystems (`Operation.require_image`). Commands whose output files
must be retrieved use `execution_result(require_image=True)`; disposable commands
can succeed without an image. Examples choose their runtime image, package their
own files, configure their workers, and print progress and execution output.

The client's submission `timeout` is the server-side execution limit; the
`wait()` deadline controls local polling and attempts cancellation when exceeded.
See the [spawn reference](https://docs.tokenfactory.nebius.com/api-reference/sandboxes/instances/spawn-a-new-container-instance).
Nebius also documents [operation event streams](https://docs.tokenfactory.nebius.com/api-reference/sandboxes/operations/stream-the-operation-event-log-via-server-sent-events)
with SSE, `follow=1`, and resume IDs. The client defaults to status polling;
the coding demo opts into streaming with `run --stream` and resumes an existing
task with `monitor --output <task-directory>`.
See [SDK compatibility](../../docs/sandbox-sdk.md) for the pinned package versions,
constructor differences, and the limited official-client fallbacks.

## Development

Use Python 3.12 for the tests and pinned development tools:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/pre-commit install --install-hooks
.venv/bin/pre-commit run --all-files --show-diff-on-failure
```

The root pre-commit configuration and CI invoke all Python tests and Ruff
configuration. The same Ruff settings also cover the Python fixture tools. No
credentials, inference requests, or live sandbox jobs are needed for these checks.

```sh
.venv/bin/ruff check --fix .
.venv/bin/ruff format .
.venv/bin/python -m unittest discover -s . -t . -v
```

If a hook fixes files, review and stage those changes before committing again.
Tests cover accounting, document errors, all three agent roles, bounded sandbox
fan-out, cancellation, and verified artifact retrieval. Development dependencies
stay separate from the dependencies installed in sandbox jobs.

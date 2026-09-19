# Nebius Token Factory Sandboxes Demos

Examples of building and hosting agents in **Nebius Token Factory Sandboxes**,
with **Nebius Token Factory** providing inference. The demos show the execution
and orchestration process: supply inputs, start sandbox jobs, collect outcomes,
and retrieve files. They run automatically, including reporting failures and
invalid inputs.

## Available demos

The current implementations are in Python. Local launchers use the official
Contree SDK and client; agent runtimes run inside the sandboxes.

| Demo | What it demonstrates | Status |
| --- | --- | --- |
| [Basic](examples/python/basic_demo/README.md) | Sandbox smoke test and a small agent that executes Python tools | Implemented |
| [Receipts](examples/python/receipt_demo/README.md) | A coordinator fans out receipt agents, collects their outcomes, starts a report agent, and returns JSON/PDF or errors | Implemented |
| [Coding agent](examples/python/codingagent_demo/README.md) | OpenCode executes a task with supplied files in a fresh sandbox, using a reusable runtime image and Token Factory inference | Implemented |

The coding demo is inspired by the task-and-result workflow in
[OpenAI's Agents API](https://developers.openai.com/api/docs/guides/agents-api/quickstart#1-run-a-task).
It provides a Python helper backed by OpenCode and Nebius Token Factory Sandboxes,
with Token Factory for inference. Its interface is specific to this demo.

It includes Create, Repair, and Extend examples, independent
correctness checks, and a deterministic timeout probe. Completion and correctness
are separate outcomes; a failed check stops the full sequence without a retry.

## Setup

Use Python 3.10+ locally and credentials with access to Nebius Token Factory
Sandboxes and Token Factory inference. From the repository root:

```sh
cd examples/python
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 -m basic_demo configure
```

The configuration command prompts for credentials and writes a gitignored
`examples/python/.env`. Sandbox and inference credentials can differ. See the
[example configuration](examples/python/codingagent_demo/.env.example) for
`CONTREE_TOKEN`, `CONTREE_PROJECT`, and `NEBIUS_API_KEY`. Shell environment values
take precedence over the file.

Check sandbox access:

```sh
python3 -m basic_demo smoke
```

All remaining demo commands below run from `examples/python`.

## Run the receipt workflow

Start with the small input set:

```sh
python3 -m receipt_demo --profile minimal --output receipt-output-minimal
```

Run the full fixture set with up to three receipt agents at once:

```sh
python3 -m receipt_demo --profile demo --concurrency 3 --output receipt-output-demo
```

The coordinator hands back the final report or errors. Flags and document failures
are automated outcomes, without a manual correction stage. See the
[receipt guide](examples/python/receipt_demo/README.md) for custom inputs, model
settings, output files, and timeout controls.

## Run the coding agent

Build the runtime once:

```sh
python3 -m codingagent_demo build-image --output coding-runtime
```

This imports a Python base image, installs pinned OpenCode and supporting tools,
and saves the resulting sandbox checkpoint. It creates
`coding-runtime/runtime.json`, containing the prepared image ID and build metadata.
Later tasks read that local file to reuse the runtime in fresh sandboxes.

Run the complete example sequence:

```sh
python3 -m codingagent_demo example all \
  --runtime coding-runtime/runtime.json \
  --output coding-output-ladder
```

The sequence creates a CSV summary script, repairs an uploaded project, extends
it with more features, and checks server timeout handling. Use `example create`,
`example repair`, `example extend`, or `example deadline` to run one stage.
Coding tasks use OpenCode with Token Factory inference; the deadline probe uses
no model. The default coding model is `moonshotai/Kimi-K2.7-Code`; override it with
`--model` or `NEBIUS_CODING_MODEL`.

For your own task, use `python3 -m codingagent_demo run` with `--task` or
`--task-file`, and repeat `--file` to supply files or folders. See the
[coding guide](examples/python/codingagent_demo/README.md) for a complete command,
the Python helper, runtime-image reuse, and outcome handling.

Each run saves its answer, status, available workspace archive, and logs in the
chosen output directory. Use a fresh output directory for each invocation.
`ladder.json` records the complete sequence; each stage has its own outcome file.
Generated runtime manifests and run artifacts remain local. Do not commit
credentials or live sandbox, project, image, or operation IDs. Documentation uses
placeholders; task plans and execution evidence belong in
[GitHub Issues](https://github.com/kreuzhofer/nebius-token-factory-sandboxes-demos/issues).

## Repository layout

```text
examples/
  python/                  Shared sandbox client, configuration, and development tools
    basic_demo/            Sandbox smoke test and Python-tool agent
    receipt_demo/          Coordinator, receipt agents, report agent, and launcher
    codingagent_demo/      Runtime image, task helper, examples, fixtures, and checks
fixtures/
  receipts/                Shared receipt inputs, provenance, and asset tools
docs/                      Shared contracts, workflows, research, and agent instructions
.github/workflows/         Repository CI
.pre-commit-config.yaml    Hook orchestration across language examples
```

Add languages under `examples/<language>/` and keep each demo in a dedicated
folder within its language. Provider API handling belongs in the shared client;
example-specific orchestration, files, dependencies, and tests belong with the
example. Receipt implementations share the [fixtures](fixtures/receipts/README.md),
[contract](docs/receipt-contract.md), and [workflow](docs/receipt-demo.md).

## Development

Use Python 3.12 for the tests and pinned development tools. From the repository
root:

```sh
python3.12 -m venv examples/python/.venv
examples/python/.venv/bin/python -m pip install -r examples/python/requirements-dev.txt
examples/python/.venv/bin/pre-commit install --install-hooks
examples/python/.venv/bin/pre-commit run --all-files --show-diff-on-failure
```

Hooks and CI run Ruff formatting/linting, file checks, and Python tests. These
checks need no credentials or live sandbox jobs. See the
[Python development guide](examples/python/README.md#development) for details.

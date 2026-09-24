# Nebius Token Factory Sandboxes demos

Run agents and their tools in **Nebius Token Factory Sandboxes**, with
**Nebius Token Factory** providing inference. These Python examples show how to
start isolated jobs, reuse prepared environments, branch from filesystem
checkpoints, monitor execution, and retrieve results.

## Choose a demo

| Demo | What you learn | Results |
| --- | --- | --- |
| [Basic](examples/python/basic_demo/README.md) | Submit a sandbox command and let a small agent execute Python tools | Command output and the agent's answer |
| [Receipt reporting](examples/python/receipt_demo/README.md) | Coordinate multiple sandbox agents with bounded concurrency | Expense report JSON and a PDF containing original receipts |
| [Coding agent](examples/python/codingagent_demo/README.md) | Reuse an OpenCode runtime, supply files, stream progress, and resume monitoring | Answer, workspace archive, logs, and independent correctness checks |
| [Data analysis](examples/python/analysis_demo/README.md) | Continue analysis in a fresh sandbox from a previous task's checkpoint | Cleaned CSV, summaries, charts, and saved analysis context |
| [Dependency upgrade](examples/python/upgrade_demo/README.md) | Branch from a working baseline, validate an upgrade, and roll back a rejected result | Selected checkpoint, project archive, and explicit validation outcomes |

## Setup

Use Python **3.10–3.14** locally; Python **3.12** is recommended and used by the
sandbox runtimes and development tools. You need credentials with access to
Sandboxes and, for agent tasks, Token Factory inference.

```sh
git clone https://github.com/kreuzhofer/nebius-token-factory-sandboxes-demos.git
cd nebius-token-factory-sandboxes-demos/examples/python
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 -m basic_demo configure
python3 -m basic_demo smoke
```

`configure` prompts for credentials and writes a gitignored
`examples/python/.env`. `CONTREE_TOKEN` supplies sandbox access and falls back to
`NEBIUS_API_KEY`; `CONTREE_PROJECT` selects the sandbox project.
`NEBIUS_API_KEY` supplies inference access. Sandbox and inference credentials can
differ. Shell environment values take precedence over the file. See the
[configuration example](examples/python/codingagent_demo/.env.example).

All commands below run from `examples/python` with the virtual environment active.
Use a fresh output directory for each run.

## How Sandboxes are used

A launcher uploads inputs and submits a command against a base image or retained
checkpoint. Submission returns an operation ID; the launcher then observes the
job and retrieves its output. The receipt demo runs its coordinator inside a
sandbox, where it starts and collects the receipt and report jobs.

A retained checkpoint contains filesystem state, including installed dependencies
and output files. Starting a child from that checkpoint creates a fresh execution
environment; it does not restore a running process or an in-memory conversation.
Children can start from the same checkpoint without changing their parent or
siblings. The analysis and upgrade demos exercise these relationships directly.

Server execution limits and local waiting deadlines are separate. Completion of
an agent task is also separate from correctness: the demos validate required
artifacts or run trusted checks before accepting results. A monitor reconnects to
an existing operation; it does not submit replacement work.

Checkpoint and event availability are subject to service retention. Download the
artifacts you need. Workspace archives contain project files and data; prepared
runtime dependencies remain in the remote checkpoint. Keep credentials,
generated artifacts, and runtime manifests out of Git.

## Run the demos

### Basic: sandbox commands and a Python-tool agent

```sh
python3 -m basic_demo smoke
python3 -m basic_demo models
# Set NEBIUS_MODEL in .env to an available model that supports tool calling.
python3 -m basic_demo agent
```

The smoke test checks sandbox execution without inference. The agent calculates
primes using a Python tool inside the sandbox. See the
[basic guide](examples/python/basic_demo/README.md) for image reuse and limits.

### Receipt reporting

```sh
python3 -m receipt_demo --profile minimal --output receipt-output-minimal
python3 -m receipt_demo --profile demo --concurrency 3 --output receipt-output-demo
```

The small profile includes a readable receipt, an unreadable total, and a corrupt
PDF. The full demo processes 14 inputs. Results include `report.json`,
`report.pdf`, and the coordinator outcome. Flags and document errors are completed
automated outcomes. See the [receipt guide](examples/python/receipt_demo/README.md)
for custom files, model settings, and artifact retrieval.

### Coding agent and live monitoring

Build the runtime once, then reuse it for tasks:

```sh
python3 -m codingagent_demo build-image --output coding-runtime
python3 -m codingagent_demo run --stream \
  --runtime coding-runtime/runtime.json \
  --task 'Fix add in proof/calculator.py, run its tests, and report the results.' \
  --file codingagent_demo/fixtures/proof \
  --timeout 300 --output coding-output-task
```

To resume observation after a lost connection or launcher restart:

```sh
python3 -m codingagent_demo monitor --output coding-output-task
```

Resume uses the saved operation ID. **Ctrl+C requests cancellation**, including
when monitoring an existing task.

Run the Create, Repair, Extend, and timeout examples:

```sh
python3 -m codingagent_demo example all \
  --runtime coding-runtime/runtime.json --output coding-output-examples
```

See the [coding guide](examples/python/codingagent_demo/README.md) for
individual examples, custom tasks, the Python helper, and result handling.

### Analysis and checkpoint follow-ups

```sh
python3 -m pip install -r analysis_demo/requirements.txt
python3 -m analysis_demo --output analysis-output-demo
```

The first task cleans sales data and generates a summary and chart. A fresh agent
starts from that task's checkpoint and produces a regional breakdown using saved
context and inherited data. Results and the last successful step are recorded in
`run/analysis.json`. See the [analysis guide](examples/python/analysis_demo/README.md)
for custom CSVs, prepared-runtime reuse, and the controlled failure scenario.

### Dependency upgrade and rollback

```sh
python3 -m upgrade_demo \
  --runtime coding-runtime/runtime.json \
  --output upgrade-output-demo
```

Omit `--runtime` to build a coding runtime automatically. Two independent agent
tasks upgrade a SQLite ledger from SQLAlchemy 1.4 to 2.0. One returns a validated
candidate. The other first passes validation, then receives a deliberately
injected regression and returns the independently verified original baseline.

The default command intentionally exits **1** because the rollback scenario
rejects an upgrade. `run/upgrade.json` reports `demonstration_complete: true` when
both intended paths succeed. See the [upgrade guide](examples/python/upgrade_demo/README.md)
for separate scenarios, execution limits, checkpoints, and archives.

## Code and development

Each demo lives under `examples/python/<demo>/` with its launcher, dependencies,
fixtures, tests, and guide. The examples share `nebius_sandbox.py` for provider
operations, `sandbox_events.py` for event streaming, and `configuration.py` for
local settings. See the [Python guide](examples/python/README.md) and
[SDK integration reference](docs/sandbox-sdk.md).

Receipt inputs and attribution are in [fixtures/receipts](fixtures/receipts/README.md);
the [workflow](docs/receipt-demo.md) and [JSON contract](docs/receipt-contract.md)
describe their outputs.

From the repository root:

```sh
python3.12 -m venv examples/python/.venv
examples/python/.venv/bin/python -m pip install -r examples/python/requirements-dev.txt
examples/python/.venv/bin/pre-commit install --install-hooks
examples/python/.venv/bin/pre-commit run --all-files --show-diff-on-failure
```

Hooks and CI run file checks, Ruff formatting/linting, and Python tests. These
checks need no credentials or live sandbox jobs.

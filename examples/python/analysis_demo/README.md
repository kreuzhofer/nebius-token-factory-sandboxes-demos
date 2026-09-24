# Analysis with checkpoint-based follow-ups

Run an automated sales analysis with Nebius Token Factory Sandboxes and Nebius
Token Factory inference. The initial coding agent cleans CSV inputs, summarizes
sales and draws a chart. A fresh agent then answers “Break this down by region”
from the initial task's filesystem checkpoint.

From `examples/python`, complete the [shared setup](../README.md#setup), configure
`NEBIUS_API_KEY` in the shared `.env` (and sandbox credentials if different), then:

```sh
.venv/bin/python -m pip install -r analysis_demo/requirements.txt
.venv/bin/python -m analysis_demo --output analysis-output-demo
```

This one command builds the shared OpenCode runtime, installs the pinned analysis
packages once, runs both analysis tasks, retrieves their answers and files, and
checks the original checkpoint independently. No manual intermediate steps are
needed. The default is OpenCode 1.18.31, Python 3.12 in the sandbox, and
`moonshotai/Kimi-K2.7-Code`. Override the model with `--model` or
`NEBIUS_CODING_MODEL`; `--timeout` sets each task's execution cap (default 600
seconds). The account must support that cap. Preparation uses two 300-second jobs;
parent verification uses a 30-second job.

The runtime's complete analysis package pins are in
[requirements-runtime.txt](requirements-runtime.txt). Builds save actual Python
and package versions in `runtime/runtime.json`. Only task sandboxes receive the
inference key. The launcher retains the sandbox API credential locally.

## Input contract

The default [synthetic fixture](fixtures/sales.csv) contains ten sales records,
including duplicate, malformed, missing-field and refund cases. It is deliberately
constructed, not real customer data. [expected.json](fixtures/expected.json)
specifies the cleaned rows and known answers independently of the agent.

Custom inputs must be UTF-8 CSV **files** with exactly this header:

```csv
sale_id,region,amount
S001,North,100.00
```

Use unique filenames; directories and symlinks are rejected. Inputs are placed
at `/workspace/<filename>`. Files are processed in filename order, then row order.
Rows must have exactly three columns. Cleaning strips field whitespace,
title-cases regions, discards missing IDs/regions and amounts that do not match
`-?[0-9]+\.[0-9]{2}`, and keeps the first valid occurrence of each sale ID across
all files. Refunds and zero are retained. At least one valid sale is required.
Amounts represent one common currency; there is no exchange-rate conversion.

The known cleaned result is **6 sales, 4 discarded records, total 215.00**:

| Region | Sales | Total |
| --- | ---: | ---: |
| East | 1 | 20.00 |
| North | 2 | 125.50 |
| South | 2 | 74.50 |
| West | 1 | -5.00 |

Supply custom requests and files with:

```sh
.venv/bin/python -m analysis_demo \
  --file /path/to/january.csv --file /path/to/february.csv \
  --task 'Clean and summarize sales; explain the refund impact.' \
  --followup 'Break this down by region and discuss the largest contributor.' \
  --output analysis-output-custom
```

Task text can request additional analysis, but must preserve the initial total
and regional follow-up output contract. This example is deliberately limited to
this sales schema and those validated aggregates. It is not a general CSV agent.
The launcher computes expected values independently for custom files too.

Use `--runtime analysis-output-demo/runtime/runtime.json` to reuse a previously
prepared **analysis** runtime in another demonstration. A coding-only runtime
lacks the analysis dependencies. Always use a fresh output directory. Checkpoints
are account-scoped and subject to service retention.

## Continuation and results

The launcher reuses `codingagent_demo.run_task` and the shared `SandboxClient`.
The initial task uses the prepared runtime; the follow-up uses the initial task's
result image. The original CSVs are not uploaded again and dependency setup is
not repeated. OpenCode starts a new process and session, without `--continue`.

The initial task creates `cleaned.csv`, a random `lineage.txt`, `summary.json` and
`chart.png` under `/workspace/steps/<unique-step-id>/`. The follow-up receives
`analysis-context.json` containing the exact preceding request, returned answer,
checkpoint and artifact references. Its prompt explicitly tells it to read that
file and the inherited cleaned data and lineage file. It writes a regional
summary and chart under its own step directory, reporting the exact lineage text
and the cleaned file's SHA-256. Neither value is supplied in the follow-up task
text (the artifact references do carry the expected content hash).

Each coding attempt also has a unique worker result directory. Inherited
summaries, charts and worker completion records cannot satisfy the new attempt.
The launcher checks execution status separately from correctness. Validation
reads only named regular files from the archive; it never extracts an archive or
executes agent-produced code on the host. It compares cleaned rows, exact totals,
regional aggregates and continuation evidence with independent expectations.
Charts must decode as nonblank bounded PNGs with step-specific `StepId` and `Data`
metadata matching the expected chart labels and totals. These checks establish
readability and provenance; they do not prove that every plotted pixel represents
the metadata. Inspect returned charts when assessing presentation quality.

`run/analysis.json` records the prepared runtime, each parent checkpoint, operation
and result checkpoint, execution status, validation result, and last successful
result. Each step directory contains the request contract, prompt, task outcome,
answer, archive, available logs, and separately saved summary/chart artifacts.
The follow-up directory also contains the saved context. `parent_verification`
records an independent read of the original archive and a disposable execution
from the original checkpoint: original artifact hashes must match, and the
follow-up's directory must be absent. That probe needs no network or credentials.

After a failed, timed-out, cancelled, interrupted or invalid follow-up, the initial
successful result remains the last successful result. The failed attempt remains
in the record with any available checkpoint and diagnostics. No task is retried
automatically; an interrupted submission may have run, so inspect its known
operation ID before deciding what to do. The command exits nonzero on any task,
validation or parent-verification failure. If the parent check itself cannot
complete, that uncertainty is explicit and the recorded successful results remain
available.

## Controlled failed follow-up

```sh
.venv/bin/python -m analysis_demo \
  --runtime analysis-output-demo/runtime/runtime.json \
  --fail-followup --output analysis-output-failure
```

This runs a normal initial analysis, then a deterministic child job that writes
`failure.txt` and reports failure. The child uses no inference or networking.
The command deliberately exits **1**. Verify that the follow-up is `failed`,
`last_successful_result.checkpoint` still matches the initial step, and
`parent_verification.passed` is true. The child's archive retains its mutation,
while the parent check confirms that mutation is absent from the original.

Generated outputs and infrastructure IDs belong in ignored `analysis-output*`
directories.
The underlying filesystem continuation model is described in the official
[branching documentation](https://docs.tokenfactory.nebius.com/sandboxes/sdk/python_sdk/branching).

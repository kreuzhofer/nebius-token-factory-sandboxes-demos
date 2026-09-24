# OpenCode on Nebius Token Factory Sandboxes

Run one unattended coding task with Token Factory inference and return its answer,
workspace archive, and logs. The local launcher uses the shared Contree SDK adapter.
OpenCode runs inside the sandbox with command/file permissions allowed. Networking
is explicitly enabled for inference and task dependencies.

The demo includes a reusable runtime-image build, an edit-and-test compatibility
proof, and the Create, Repair, Extend, and deadline examples.

## Build the runtime once

From `examples/python`, configure the shared `.env` using the names in
[.env.example](.env.example), and complete the [shared setup](../README.md#setup)
to install the SDK dependencies. Then run:

```sh
python3 -m codingagent_demo build-image --output coding-runtime
```

The build imports `python:3.12-slim` unless `--base-image UUID` is supplied, then
runs a normal sandbox job that installs Git, ripgrep and OpenCode **1.18.31**.
The Linux x64 baseline binary comes from the official npm package; its archive
is checked against a pinned SHA-512 digest and its version is verified.

The sandbox service preserves that job's filesystem because it is not disposable.
The resulting image UUID is saved in `coding-runtime/runtime.json`, together with
the build operation and base image. This is the API's import/run/checkpoint flow,
not a Dockerfile build service. See the
[sandbox overview](https://docs.tokenfactory.nebius.com/sandboxes/overview) and
[spawn API](https://docs.tokenfactory.nebius.com/api-reference/sandboxes/instances/spawn-a-new-container-instance).

No inference credentials are passed to the build. Subsequent task jobs start from
this image and do not download or install the OpenCode binary. Each job gets a
fresh workspace; task outputs are not used as the base for the next task.

## Check runtime reuse

```sh
python3 -m codingagent_demo proof \
  --runtime coding-runtime/runtime.json \
  --output coding-output-proof
```

The proof asks OpenCode to repair the uploaded `calculator.py` and run its tests.
It retrieves the edited code, then runs trusted copies of the tests in a separate
validation sandbox without networking or inference credentials. It repeats with
a fresh task sandbox from the same runtime image. This verifies image reuse and
keeps test results separate from the agent's claim of success.

`proof.json` records both task outcomes and independent validation results.
The command exits nonzero if either task or validation fails.

## Run a coding example

```sh
python3 -m codingagent_demo example create \
  --runtime coding-runtime/runtime.json \
  --output coding-output-create
```

Use `example repair` with a fresh output directory to diagnose and fix the
uploaded multi-file project. Its original tests exercise decimal arithmetic,
refunds, and quoted CSV fields. `example extend` starts from the maintained
correct project and adds directory input, JSON/CSV output, and invalid-row
reporting. Each stage uses independent inputs and a fresh sandbox.

Create uploads a CSV and asks the agent to write and execute its summary script.
The returned archive is unpacked only inside a separate validation sandbox.
`example.json` records the task outcome and independent checks; the command
exits nonzero if either fails. Run results stay in the local output directory.

Run the complete sequence, stopping at the first failed task or check:

```sh
python3 -m codingagent_demo example all \
  --runtime coding-runtime/runtime.json \
  --model moonshotai/Kimi-K2.7-Code \
  --output coding-output-ladder
```

`ladder.json` links the per-stage outcomes. No task is automatically retried.
The Create, Repair, and Extend server caps are 300, 600, and 1800 seconds.
A model's final response is not a guarantee that its code passes the checks.

Use `example deadline` to run the deterministic timeout probe alone. It needs
only sandbox credentials: networking is disabled and no inference key is sent.
The command writes a startup file and archive before sleeping for 60 seconds
under a 15-second server cap. `deadline.json` reports the confirmed timeout and
whether that archive could be recovered. It exercises the same workspace-job
submission, waiting, status, and artifact-recovery path as the coding tasks.
`process.stdout.log` and `process.stderr.log` retain available process output.
The probe is a lifecycle check, not a coding task padded with a sleep.

## Run your own task

```sh
python3 -m codingagent_demo run \
  --runtime coding-runtime/runtime.json \
  --task 'Fix the add function in proof/calculator.py. Run the tests and report the output.' \
  --file codingagent_demo/fixtures/proof \
  --timeout 300 \
  --output coding-output-task
```

Use `--image UUID` instead of `--runtime` to select an existing prepared image.
Repeat `--file` for multiple inputs, or use `--task-file PATH` for a longer prompt.
An input file is placed at `/workspace/<filename>`; a directory preserves its
name and contents. Symlinks are rejected. `.git`, `.venv`, `__pycache__`,
`node_modules` and `.env` entries are omitted from input directories.
Use a new output directory for each run.

The default model is `moonshotai/Kimi-K2.7-Code`; override it with
`--model` or `NEBIUS_CODING_MODEL`. Main and auxiliary models use the configured
Token Factory provider. Only the inference key enters the task sandbox; the
sandbox API token remains with the launcher. Agent configuration lives outside
the workspace. The archived workspace excludes the runtime and OpenCode's
session/cache directories.

The helper checks the account's reported maximum timeout before submission.
The worker reserves 30 seconds of the requested execution time for packaging
and stops the agent process group if its deadline expires. The client permits
another 120 seconds for operation completion and checkpoint retrieval.
Without `--stream`, progress shows operation-state changes; detailed agent events are
retrieved when the job finishes.

## Results and failures

Each run writes `job.json` as soon as the operation ID is known, followed by
`result.json`, `workspace.tar.gz`, `events.jsonl` and `stderr.log` when available.
`result.json` includes status, answer, operation/image IDs, model/runtime versions,
elapsed time and artifact paths. Elapsed time covers submission, waiting, and
retrieval; input upload happens before that timer. Downloaded archives are saved
without executing or extracting their contents on the host.

Completed means the agent finished normally and the archive was retrieved;
correctness is a separate check. Failed, timed-out, and confirmed-cancelled jobs
are reported explicitly. A connection loss or an unconfirmed cancellation returns
`interrupted` with the known operation ID. Nothing automatically resubmits the
task. Files and logs after failure are best-effort; checkpoint availability is
not guaranteed. Invalid arguments and setup errors can raise before submission.

Python callers can use the same interface:

```python
from codingagent_demo import AgentConfig, run_task

# client is an authenticated nebius_sandbox.SandboxClient.
result = run_task(
    client,
    AgentConfig(api_key=inference_key),
    image=prepared_image_uuid,
    task="Fix the uploaded project and run its tests.",
    files=[project_directory],
    output="coding-output-task",
    timeout=300,
)
```

## Runtime compatibility

Image availability is scoped to the account and service retention. Build a new
image when reproducing elsewhere or changing the runtime.

The pinned CLI's [run command source](https://github.com/anomalyco/opencode/blob/v1.18.31/packages/opencode/src/cli/cmd/run.ts)
provides JSON events and disables interactive question/plan transitions in
headless mode. Provider configuration follows the matching
[OpenCode provider documentation](https://opencode.ai/docs/providers/).

The [analysis demo](../analysis_demo/README.md) uses the same helper with an
initial task checkpoint as its follow-up image and supplies explicit saved context.
Worker results use a unique directory per attempt so inherited results cannot be
mistaken for the current outcome. The coding examples above still begin each task
from the prepared runtime.

## Live monitoring and restart recovery

On macOS or Linux, opt into readable agent messages, tool activity and sandbox
lifecycle events while a task runs:

```sh
python3 -m codingagent_demo run --stream \
  --runtime coding-runtime/runtime.json \
  --task 'Fix add in proof/calculator.py, run its tests, and report the results.' \
  --file codingagent_demo/fixtures/proof \
  --timeout 300 --output coding-output-stream
```

The worker publishes output before completion and redacts the inference key
before writing logs or sending live bytes, including keys split across pipe
reads. Unrecognized process output and stderr remain visible; the full underlying
sandbox events are retained in `transcript.jsonl`. Terminal control characters
are removed from the readable presentation.

If the launcher crashes or its connection is lost, resume the **same operation**:

```sh
python3 -m codingagent_demo monitor --output coding-output-stream
```

Use the task directory containing `job.json`. Resume needs sandbox credentials;
it does not need to supply the inference key again. It never uploads inputs or
submits another job. It retrieves the normal answer, workspace archive and logs.
It does not continue a surrounding analysis workflow or change that workflow's
last successful result. Older task records without an original deadline cannot
be resumed by this command.

The transcript is append-only, and each complete event is flushed to disk before
it is printed. Resume reconstructs partial messages from the saved events and
requests events after the last saved ID. Duplicate events are skipped. A torn
final append is discarded so the unfinished event can be replayed. Saving and
printing cannot be atomic: a crash may leave an event saved but never printed;
it remains available in the transcript. Only one monitor may write a task
output directory at a time, enforced by an OS file lock released on process exit.

The shared adapter reconnects after retryable stream errors, with exponential
backoff and `Retry-After` support. After five consecutive failures without event
progress, it falls back to status polling and flags the live transcript as
potentially incomplete. Authentication errors, missing operations and invalid
protocol data are reported without blind retries. All requests remain bound by
the original waiting deadline; reconnecting and restarting do not extend it.
Completed results can still be recovered after that deadline. If a resumed job
is still running after its deadline, cancellation is requested.

**Ctrl+C requests cancellation**, both when launching and when resuming. It is
not a detach command. Unconfirmed cancellation and connection loss preserve the
operation ID and return an interrupted outcome; the server execution cap remains
active. The monitor can subsequently recover available results from that ID.

Task success still depends on the operation, process and required final artifacts.
Missing live output does not by itself turn a successful task into a failure.
`job.json` and `result.json` include a `monitoring` section with the last event
cursor, transcript path, whether the completion event was seen, transcript
completeness and warnings. Output limits can truncate the transcript even when
execution succeeds. The full worker logs are retrieved from the checkpoint when
available. Event and checkpoint retention are service-controlled; resume cannot
guarantee recovery after they expire.

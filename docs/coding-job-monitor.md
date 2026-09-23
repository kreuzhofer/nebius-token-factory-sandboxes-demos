# Coding-job monitor: accepted design for issue #34

Accepted during the design interview on 2026-09-23. This brief supplements
[issue #34](https://github.com/kreuzhofer/nebius-token-factory-sandboxes-demos/issues/34)
and records the decisions needed for implementation. The recovery boundary and
display trade-off are recorded in [ADR 0001](adr/0001-resume-monitoring-without-restarting-work.md).

## Commands and scope

- `python -m codingagent_demo run --stream ...` opts into live monitoring.
- `python -m codingagent_demo monitor --output <existing-task-directory>` resumes
  observation and result retrieval for the saved coding task.
- Existing commands retain their default behavior.
- Reconnection and launcher restart reuse the saved operation identity. Neither
  path submits replacement work.
- Recovery covers one coding task. It does not continue an analysis workflow,
  launch its next step, or advance its last successful analysis result.

## Presentation and persistence

Show readable agent messages, tool activity, stdout/stderr and sandbox lifecycle
changes. Retain the underlying events for diagnostics, with credentials redacted
before live publication or persistence. Output chunks need not align with text
lines or agent messages.

Save each event durably before displaying it. Resume after the last saved event
and suppress replay duplicates. Saving and terminal output cannot be atomic: an
abrupt crash can leave a saved event that was never printed. This is accepted;
the event remains available in the transcript.

Only one active monitor may write a task's output directory. Reject a second
writer clearly. Persist the operation identity, event position and original
deadline information needed to resume safely.

## Reconnection, cancellation and results

Use bounded exponential backoff for retryable stream failures and honor
`Retry-After`. After five consecutive retryable failures without progress, fall
back to operation-status polling and clearly report the interruption. Do not
retry sooner than the service allows or extend the original waiting deadline.
Authentication failures, missing operations and invalid protocol data must be
reported explicitly rather than retried blindly.

Ctrl+C requests cancellation in both launching and resumed monitors. Preserve
the distinction between confirmed cancellation and an unconfirmed cancellation
request. Restarting a monitor does not reset the execution or waiting budget.
A resumed monitor first checks current status: completed results can be recovered
after the former waiting deadline, while an overdue operation that is still
running follows the existing cancellation policy.

Determine the task outcome from operation status, process results and retrieval
of required artifacts. Live output alone never establishes success. Attempt to
drain remaining events at completion within the available budget, and report
transcript gaps separately. A completed task with its required artifacts can
succeed despite an incomplete transcript. Preserve the normal final answer,
workspace archive, diagnostics and failure classifications.

## Verified implementation constraints

The current worker writes OpenCode output to files and publishes only a final
status on sandbox stdout. Live agent activity therefore requires a worker change,
including redaction before publication rather than only at completion.

The official [event endpoint documentation](https://docs.tokenfactory.nebius.com/api-reference/sandboxes/operations/stream-the-operation-event-log-via-server-sent-events)
and [OpenAPI schema](https://eu-north.nebius.computer/static/api.yaml) establish:

- Event IDs increase and can start at zero. `since=N` replays IDs greater than N;
  `Last-Event-Id` takes precedence. Initial attachment must omit the cursor.
- `follow=1` replays available events and follows a running operation. A terminal
  operation replays its log and closes the stream.
- `completion` is the operation's terminal event. A process `exit` or `shutdown`
  event does not establish operation completion.
- stdout/stderr contain ASCII or base64 byte chunks. `sse_error` carries plain
  text and signals a broken stream.
- Documented retryable responses include 410, 425, 502 and 504. Here, 410 can mean
  temporary terminal-log finalization; it does not establish permanent expiry.
- `truncated` and `size_cap` signal output loss. No complete-output guarantee or
  fixed event-retention duration was found in the inspected documentation.

The installed `contree-client==0.4.0` exposes an event iterator. Its higher-level
follow helper catches all exceptions and may stop on terminal status before
draining the remaining log, so its behavior must not be assumed to satisfy this
contract. Generic provider handling belongs in the shared sandbox adapter;
coding-agent presentation and task recovery belong in the coding demo.

## Agreed test boundaries and acceptance

Use TDD at these confirmed boundaries:

- Shared sandbox streaming interface, exercised at the HTTP boundary: framing,
  keepalives, chunk decoding, zero/exclusive cursors, duplicate events, retryable
  responses, terminal events and cancellation/deadline behavior.
- Public coding run/monitor commands and task outcomes: one submission across
  reconnects and restarts, saved event position, polling fallback, exclusive
  output-directory ownership, result recovery and separate transcript warnings.
- Worker process output: activity published before completion, messages spanning
  chunks, credential redaction and preserved final artifacts.

Run the repository checks and record live evidence on issue #34, including a
controlled dropped stream and restart recovery against the same operation.
Keep generated results, credentials and infrastructure IDs out of tracked files.

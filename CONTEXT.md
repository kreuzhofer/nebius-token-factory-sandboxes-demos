# Agent demos

These demos use Nebius Token Factory Sandboxes for agent execution and Nebius
Token Factory for inference. They cover receipt reporting, coding tasks, data
analysis, and dependency upgrades, with explicit results and failure outcomes.

## Language

### Platform

**Nebius Token Factory Sandboxes**:
The product that hosts agent execution in isolated environments. Use this full
name for the product; an individual execution environment is a sandbox.

**Nebius Token Factory**:
The inference platform used by the agents. Token Factory is the short form used
after introducing the full name.

### Receipt reporting

**Coordinator agent**:
The agent that enumerates receipts, dispatches receipt agents, collects their
outcomes, and requests the report. It retrieves and returns the final report or
errors to the caller.

**Receipt agent**:
An agent that processes one receipt and returns structured fields or a flagged
failure. Multiple receipt-agent instances share the same implementation.

**Report agent**:
The agent that reconciles collected receipt outcomes and generates the expense
report artifacts, returning them to the coordinator.

**Receipt**:
A source document recording a purchase or refund; it may span multiple pages.

**Parsed receipt**:
The extracted content, structure and expense fields of a receipt, linked to its
source evidence and any parsing issues.

**Reconciliation**:
Checking receipts for duplicates, monetary inconsistencies and missing information,
and organizing their expenses. It does not mean bank-statement matching or
reimbursement approval in this example.

**Flagged item**:
A receipt or field that could not be processed reliably and is flagged in the
final report as a completed automated outcome. It does not enter a human queue.
_Avoid_: Review item, pending review

**Expense report**:
A summary of reconciled expenses and flagged items, followed by an appendix of
original receipt pages and their extracted fields.

**Shared receipt corpus**:
The project-provided input receipts reused by the language examples to demonstrate
the automated workflow, including representative successful and problematic inputs.

**Synthetic receipt**:
A deliberately constructed receipt with known source values, labelled as synthetic
so it remains distinguishable from a real receipt.

**Receipt variant**:
A duplicate or altered representation of a source receipt used to exercise
processing behavior; it retains that receipt's identity rather than counting as
another independent example.

**Expected results**:
Known values and outcomes used for lightweight checks of the demo's behavior.
Values unreadable in the supplied evidence remain explicitly unknown, even when
the underlying synthetic source values are known.

### Coding tasks

**Coding agent**:
An agent that changes and executes code to fulfill a task using the supplied
files, returning its answer and the resulting workspace.

**Coding task**:
One requested unit of coding work, described by instructions and explicitly
supplied files or folders. Each task begins in a fresh workspace.

**Task outcome**:
The coding agent's execution status, answer, available workspace files, and
diagnostics. Completion means the agent finished; passing correctness checks is
a separate result.

**Coding-job monitor**:
The observer of an existing coding task that presents live agent messages, tool
activity and sandbox lifecycle changes. Reconnecting or restarting the monitor
does not start another coding task.

**Task transcript**:
The recorded events from a coding task, retained for diagnostics alongside its
readable live presentation. Missing transcript events are reported separately
from the task's final outcome; an incomplete transcript does not imply task failure.

### Data analysis

**Analysis step**:
One requested analysis of supplied data or a follow-up to a preceding analysis.
Its execution outcome and the correctness of its returned artifacts are separate results.

**Prepared analysis runtime**:
A reusable environment containing the agent and analysis dependencies before any
analysis task has run.

**Analysis checkpoint**:
The retained filesystem after an analysis step. A continuation starts a fresh
execution environment from this checkpoint; it does not restore a running process.

**Saved analysis context**:
The preceding request, answer and artifact references explicitly supplied to a
follow-up. It accompanies the checkpoint instead of relying on an in-memory conversation.

**Last successful analysis result**:
The most recent step whose execution succeeded and whose required artifacts passed
validation. A failed or unconfirmed continuation does not replace it.

### Dependency upgrades

**Upgrade baseline**:
The working project and dependency environment retained before an upgrade, with
passing fixture tests.

**Upgrade candidate**:
The project and dependency environment produced by attempting an upgrade in a
separate branch of the upgrade baseline.

**Upgrade rollback**:
Selecting the unchanged upgrade baseline after rejecting an upgrade candidate.
Verified rollback establishes that baseline tests still pass and candidate changes
are absent from the baseline.

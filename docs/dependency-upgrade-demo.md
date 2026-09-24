# Dependency-upgrade demo

Implementation brief for [issue #31](https://github.com/kreuzhofer/nebius-token-factory-sandboxes-demos/issues/31).
Design and shared understanding confirmed on 2026-09-24. See the
[demo guide](../examples/python/upgrade_demo/README.md) for commands and result
formats.

## Outcome and fixture

Demonstrate a Token Factory-backed coding agent upgrading a real dependency in
Nebius Token Factory Sandboxes. Return a validated upgrade candidate or select the
unchanged upgrade baseline, with explicit validation and recovery outcomes.

Use a small Python SQLite expense ledger upgrading SQLAlchemy from `1.4.54` to
`2.0.36`. Migrate the removed `Engine.execute` API and implicit autocommit;
passing must require a compatibility edit, making the agent's contribution
observable. See the [SQLAlchemy migration guide](https://docs.sqlalchemy.org/en/20/changelog/migration_20.html).

Seed the baseline database with known ledger entries and verify the baseline
before either upgrade attempt. Deterministic offline tests check totals,
persistence after reopening the database, preservation of pre-existing entries,
and behavior for new transactions.

## Branching and authoritative validation

Both demonstration scenarios start independently from the same verified baseline.
The rollback scenario cannot inherit changes from the successful scenario.

Run authoritative validation in a fresh, disposable child of the candidate
checkpoint, using trusted tests supplied separately from the agent's files.
Validate the installed dependency version and project pin as well as behavior,
so retaining the old dependency cannot count as a successful upgrade. Validate
the actual inherited environment rather than reconstructing it from source.

Validation writes stay in disposable children. Select the validated candidate
checkpoint as the successful result, without contaminating its database with
test writes. A candidate without completed, passing authoritative validation
cannot become the successful result.

## Passing and controlled rollback scenarios

Both scenarios execute a real coding agent. A genuine agent failure triggers
rollback; never substitute a known-good solution into the live result.

The controlled rollback scenario first proves the agent's upgrade passes. Then
inject a clearly identified regression into the candidate branch and validate
again, retaining evidence of both checks. This establishes that the injected
regression caused rejection. If the agent fails before injection, return the
baseline and report a genuine upgrade failure; mark the controlled rollback
demonstration incomplete.

Use a known-good solution in automated orchestration tests to exercise the
passing path deterministically. Live acceptance evidence requires an actual
successful agent run as well as the controlled rollback demonstration.

## Rollback and failure reporting

Rollback selects the unchanged baseline instead of reversing edits in the
candidate. Independently verify that baseline tests pass and candidate changes
are absent from it. Upgrade success, recovery success, and completion of the
intended demonstration scenario are separate results.

| Result | Returned environment | Command outcome |
| --- | --- | --- |
| Upgrade accepted | Validated candidate checkpoint | Success |
| Upgrade rejected; baseline verified | Unchanged, independently verified baseline | Nonzero exit |
| Upgrade rejected; baseline verification incomplete | Retained baseline reference, without a claim of verified recovery | Nonzero exit |
| Upgrade rejected; baseline tests fail | Retained baseline reference and failure details, without a claim of verified recovery | Nonzero exit |

If baseline verification cannot finish, report "upgrade rejected; baseline
verification incomplete." Retain the baseline reference and available diagnostics.
Distinguish verification infrastructure errors from baseline tests actually
failing.

## Execution limits and local results

Allow one agent task per scenario, capped at 600 seconds. The agent may edit and
test repeatedly within that task; the orchestrator does not launch replacement
attempts automatically. Inherit the existing bounded validation and monitoring
behavior. There is no human approval stage in the running demo.

Return a local JSON report identifying the selected checkpoint, checkpoint and
branch relationships, validation results, and explicit outcome, together with
a project/data archive and available agent and validation logs. The checkpoint
retains the installed environment; the archive contains project files and data.
Retain failed-branch diagnostics as well as the selected result.

## Implementation and acceptance boundaries

Keep example-specific files in a dedicated folder under `examples/python/` and
shared provider handling in the shared sandbox client. Reuse existing coding
agent and monitoring helpers where appropriate.

Document commands for both scenarios. Run focused tests and repository checks,
and record live execution evidence in issue #31. Keep generated artifacts,
credentials, and live infrastructure IDs out of Git; show checkpoint and branch
relationships in local results.

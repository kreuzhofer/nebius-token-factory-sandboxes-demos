# Resume monitoring without restarting work

Accepted on 2026-09-23. See the [implementation brief](../coding-job-monitor.md)
for commands, retry policy and agreed test boundaries.

Issue #34 resumes observation and result retrieval for one already-submitted coding
task. The monitor retains the task's identity and event position across restarts;
reconnecting never submits replacement work or continues a surrounding analysis
workflow. This keeps recovery safe when execution may have completed despite a
lost connection, and preserves the distinction between a task outcome and the
analysis workflow's independently validated last successful result.

Both a launching monitor and a resumed monitor request cancellation on Ctrl+C.
Stream reconnection failures fall back to status polling under the original
deadlines, rather than granting the task a new execution or waiting budget.

Events are saved before display, and resumed monitoring starts after the last
saved event. Saving an event and printing it cannot be atomic: an abrupt crash
can leave an event in the transcript that never appeared on screen. We accept
that limitation to avoid replaying saved events after restart. Missing live
output is reported separately from the task outcome, which still depends on
operation status, process results and retrieval of the required artifacts.

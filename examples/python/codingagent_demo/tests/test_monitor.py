"""Public coding-task recovery and display contracts at the sandbox boundary."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from http_transport import TransportError
from nebius_sandbox import Operation
from sandbox_events import wait_with_events

from codingagent_demo import AgentConfig, run_task
from codingagent_demo.job import monitor_task
from codingagent_demo.tests.test_run import TaskService


def event(number, text):
    return {"id": number, "type": "stdout", "spid": 1, "data": {"value": text, "encoding": "ascii"}}


class StreamingService(TaskService):
    wait_with_events = wait_with_events

    def __init__(self):
        super().__init__()
        self.crash = True
        self.cursors = []
        self.cancelled = []
        self.finished = False

    def get_operation(self, operation):
        if self.finished:
            return self.wait(operation, 0)
        return Operation.from_response(operation, {"status": "EXECUTING"})

    def operation_events(self, operation, *, after, deadline):
        self.cursors.append(after)
        if self.finished:
            raise AssertionError("A complete saved transcript needs no replay")
        yield event(0, '{"type":"text","part":{"text":"first message"}}\n')
        if self.crash:
            raise TransportError("status connection lost")
        yield event(1, '{"type":"text","part":{"text":"second message"}}\n')
        self.finished = True
        yield {"id": 2, "type": "completion", "data": {"status": "SUCCESS"}}

    def cancel(self, operation):
        self.cancelled.append(operation)


class MonitorTests(unittest.TestCase):
    def test_invalid_output_is_not_saved_and_resume_can_retrieve_corrected_event(self):
        class InvalidOutput(StreamingService):
            def operation_events(self, operation, *, after, deadline):
                self.cursors.append(after)
                yield event(0, "first\n")
                if self.crash:
                    yield {"id": 1, "type": "stdout", "data": {"encoding": "unknown"}}
                yield event(1, '{"type":"tool_use","part":{"state":42}}\n')
                self.finished = True
                yield {"id": 2, "type": "completion", "data": {}}

        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            api = InvalidOutput()
            output = Path(directory) / "task"
            first = run_task(api, AgentConfig("key"), "runtime", "Work", output=output, stream=True)
            self.assertEqual(first["status"], "interrupted")
            self.assertEqual(first["monitoring"]["cursor"], 0)
            api.crash = False
            result = monitor_task(api, output)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(api.cursors, [None, 0])
            self.assertEqual(len(api.submissions), 1)

    def test_missing_events_warn_without_changing_outcome_and_survive_restart(self):
        class MissingEvents(StreamingService):
            def operation_events(self, operation, **options):
                yield event(0, "first\n")
                self.finished = True
                yield {"id": 2, "type": "completion", "data": {}}

        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            api = MissingEvents()
            output = Path(directory) / "task"
            result = run_task(
                api, AgentConfig("key"), "runtime", "Work", output=output, stream=True
            )
            self.assertEqual(result["status"], "completed")
            self.assertFalse(result["monitoring"]["complete"])
            self.assertIn("missing event", str(result["monitoring"]["warnings"]))
            # Simulate a crash after journal fsync but before warning state was saved.
            saved = json.loads((output / "job.json").read_text())
            saved["monitoring"]["warnings"] = []
            (output / "job.json").write_text(json.dumps(saved))
            result = monitor_task(api, output)
            self.assertEqual(result["status"], "completed")
            self.assertFalse(result["monitoring"]["complete"])
            self.assertIn("missing event", str(result["monitoring"]["warnings"]))

    def test_restart_recovers_same_operation_without_redisplay_or_resubmission(self):
        api = StreamingService()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "task"
            first_display = io.StringIO()
            with contextlib.redirect_stdout(first_display):
                first = run_task(
                    api, AgentConfig("key"), "runtime", "Work", output=output, stream=True
                )
            self.assertEqual(first["status"], "interrupted")
            deadline = json.loads((output / "job.json").read_text())["deadline_at"]
            self.assertIn("first message", first_display.getvalue())
            api.crash = False
            second_display = io.StringIO()
            with contextlib.redirect_stdout(second_display):
                second = monitor_task(api, output)
            self.assertEqual(second["status"], "completed")
            self.assertEqual(second["answer"], "Fixed and tested.")
            self.assertNotIn("first message", second_display.getvalue())
            self.assertIn("second message", second_display.getvalue())
            self.assertEqual(api.cursors, [None, 0])
            self.assertEqual(len(api.submissions), 1)
            self.assertEqual(api.cancelled, [])
            self.assertEqual(second["deadline_at"], deadline)
            transcript = [
                json.loads(line) for line in (output / "transcript.jsonl").read_text().splitlines()
            ]
            self.assertEqual([e["id"] for e in transcript], [0, 1, 2])

    def test_truncated_live_output_does_not_replace_a_successful_task_outcome(self):
        class Truncated(StreamingService):
            def __init__(self):
                super().__init__()
                self.crash = False

            def operation_events(self, operation, **options):
                yield {"id": 0, "type": "truncated", "data": {"stream": "stdout"}}
                yield {"id": 1, "type": "completion", "data": {"status": "SUCCESS"}}

            def wait(self, operation, seconds, **options):
                return Operation.from_response(
                    operation,
                    {
                        "status": "SUCCESS",
                        "result_image_uuid": "task-image",
                        "metadata": {
                            "result": {
                                "state": {"exit_code": 0},
                                "stdout": {
                                    "truncated": True,
                                    "encoding": "ascii",
                                    "value": "partial output",
                                },
                            }
                        },
                    },
                )

        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            result = run_task(
                Truncated(),
                AgentConfig("key"),
                "runtime",
                "Work",
                output=Path(directory) / "task",
                stream=True,
            )
            self.assertEqual(result["status"], "completed")
            self.assertTrue(Path(result["archive"]).exists())
            self.assertTrue(result["monitoring"]["warnings"])
            self.assertFalse(result["monitoring"]["complete"])

    def test_completed_transcript_can_be_reopened_without_a_false_gap_warning(self):
        api = StreamingService()
        api.crash = False
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            output = Path(directory) / "task"
            run_task(api, AgentConfig("key"), "runtime", "Work", output=output, stream=True)
            result = monitor_task(api, output)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(api.cursors, [None])
            self.assertEqual(result["monitoring"]["warnings"], [])

    def test_a_second_monitor_is_rejected_without_altering_the_running_task(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            output = Path(directory) / "task"
            failures = []

            class CompetingMonitor(StreamingService):
                def operation_events(self, operation, **options):
                    try:
                        monitor_task(self, output)
                    except RuntimeError as exc:
                        failures.append(str(exc))
                    yield from super().operation_events(operation, **options)

            api = CompetingMonitor()
            api.crash = False
            result = run_task(
                api, AgentConfig("key"), "runtime", "Work", output=output, stream=True
            )
            self.assertEqual(result["status"], "completed")
            self.assertEqual(failures, ["Another monitor already owns this task directory"])
            self.assertEqual(len(api.submissions), 1)

    def test_expired_resume_recovers_terminal_result_but_cancels_running_task(self):
        for finished in (True, False):
            with (
                self.subTest(finished=finished),
                tempfile.TemporaryDirectory() as directory,
                contextlib.redirect_stdout(io.StringIO()),
            ):
                api = StreamingService()
                output = Path(directory) / "task"
                run_task(api, AgentConfig("key"), "runtime", "Work", output=output, stream=True)
                saved = json.loads((output / "job.json").read_text())
                saved["deadline_at"] = 1
                (output / "job.json").write_text(json.dumps(saved))
                api.finished = finished
                result = monitor_task(api, output)
                self.assertEqual(result["deadline_at"], 1)
                self.assertEqual(result["status"], "completed" if finished else "interrupted")
                self.assertEqual(api.cancelled, [] if finished else ["task-job"])
                self.assertEqual(len(api.submissions), 1)

    def test_restart_reconstructs_partial_messages_and_discards_only_torn_final_append(self):
        class Fragmented(StreamingService):
            def operation_events(self, operation, *, after, deadline):
                self.cursors.append(after)
                yield event(0, '{"type":"text","part":{"text":"across')
                if self.crash:
                    raise TransportError("connection lost")
                yield event(1, ' restart"}}\n')
                self.finished = True
                yield {"id": 2, "type": "completion", "data": {"status": "SUCCESS"}}

        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            api = Fragmented()
            output = Path(directory) / "task"
            run_task(api, AgentConfig("key"), "runtime", "Work", output=output, stream=True)
            with (output / "transcript.jsonl").open("ab") as transcript:
                transcript.write(b'{"id":1,"ty')
            api.crash = False
            rendered = io.StringIO()
            with contextlib.redirect_stdout(rendered):
                result = monitor_task(api, output)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(rendered.getvalue().count("Agent: across restart"), 1)
            self.assertEqual(api.cursors, [None, 0])

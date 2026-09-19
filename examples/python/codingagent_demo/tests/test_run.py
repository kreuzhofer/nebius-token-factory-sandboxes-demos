"""Observe the task/result boundary without making model calls."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from nebius_sandbox import Operation

from codingagent_demo import AgentConfig, run_task


class TaskService:
    def __init__(self):
        self.submissions = []
        self.uploads = []
        self.result = {"status": "completed", "answer": "Fixed and tested.", "error": None}

    def limits(self):
        return {"instance_max_timeout": 3600}

    def upload(self, content):
        self.uploads.append(content)
        return {"uuid": hashlib.sha256(content).hexdigest(), "mode": "0600"}

    def submit(self, image, **options):
        self.submissions.append((image, options))
        return "task-job"

    def wait(self, operation, seconds, **options):
        return Operation.from_response(
            operation,
            {
                "status": "SUCCESS",
                "result_image_uuid": "task-image",
                "metadata": {"result": {"state": {"exit_code": 0}}},
            },
        )

    def download(self, image, path):
        if path.endswith("result.json"):
            return json.dumps(self.result).encode()
        if path.endswith("workspace.tar.gz"):
            return b"archive-bytes"
        return b'{"type":"text","part":{"text":"Fixed and tested."}}\n'


class RunTests(unittest.TestCase):
    def test_task_returns_answer_artifacts_and_known_job_using_existing_runtime(self):
        api = TaskService()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.py"
            source.write_text("print('before')\n")
            result = run_task(
                api,
                AgentConfig("inference-secret"),
                "runtime-image",
                "Fix the uploaded file",
                files=[source],
                output=root / "result",
                timeout=300,
            )
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["answer"], "Fixed and tested.")
            self.assertEqual(result["operation_id"], "task-job")
            self.assertTrue(Path(result["archive"]).is_file())
            self.assertEqual(
                json.loads((root / "result" / "job.json").read_text())["operation_id"], "task-job"
            )
            image, options = api.submissions[0]
            self.assertEqual(image, "runtime-image")
            self.assertNotIn("CONTREE_TOKEN", options["env"])
            self.assertIn("/workspace/input.py", options["files"])
            self.assertFalse(any(b"inference-secret" in content for content in api.uploads))
            self.assertEqual(len(api.submissions), 1)

    def test_missing_archive_is_reported_instead_of_a_complete_result(self):
        from http_transport import TransportError

        class MissingArchive(TaskService):
            def download(self, image, path):
                if path.endswith("workspace.tar.gz"):
                    raise TransportError("GET request failed: HTTP 404")
                return super().download(image, path)

        with tempfile.TemporaryDirectory() as directory:
            result = run_task(
                MissingArchive(),
                AgentConfig("key"),
                "runtime",
                "Work",
                output=Path(directory) / "result",
            )
            self.assertEqual(result["status"], "failed")
            self.assertIsNone(result["archive"])
            self.assertIn("archive", result["error"].lower())

    def test_connection_loss_preserves_identity_and_does_not_resubmit(self):
        from http_transport import TransportError

        class Disconnected(TaskService):
            def wait(self, operation, seconds, **options):
                raise TransportError("GET request failed: connection unavailable")

        api = Disconnected()
        with tempfile.TemporaryDirectory() as directory:
            result = run_task(
                api, AgentConfig("secret"), "runtime", "Work", output=Path(directory) / "result"
            )
            self.assertEqual(result["status"], "interrupted")
            self.assertEqual(result["operation_id"], "task-job")
            self.assertEqual(len(api.submissions), 1)
            self.assertIsNone(result["archive"])

    def test_unavailable_summary_still_recovers_workspace_and_logs(self):
        from http_transport import TransportError

        class BrokenSummary(TaskService):
            def __init__(self, missing):
                super().__init__()
                self.missing = missing

            def download(self, image, path):
                if path.endswith("result.json"):
                    if self.missing:
                        raise TransportError("GET request failed: HTTP 404")
                    return b"invalid json"
                return super().download(image, path)

        for missing in (True, False):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as directory:
                result = run_task(
                    BrokenSummary(missing),
                    AgentConfig("secret"),
                    "runtime",
                    "Work",
                    output=Path(directory) / "result",
                )
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["image"], "task-image")
                self.assertEqual(Path(result["archive"]).read_bytes(), b"archive-bytes")
                self.assertEqual(len(result["logs"]), 2)
                self.assertIn("summary", result["error"])

    def test_failed_process_and_confirmed_timeout_are_not_completion(self):
        for state, expected in [
            ({"exit_code": 1}, "failed"),
            ({"exit_code": -1, "timed_out": True}, "timed_out"),
        ]:

            class FailedProcess(TaskService):
                process_state = state

                def wait(self, operation, seconds, **options):
                    return Operation.from_response(
                        operation,
                        {
                            "status": "SUCCESS",
                            "metadata": {"result": {"state": self.process_state}},
                        },
                    )

            with self.subTest(state=state), tempfile.TemporaryDirectory() as directory:
                result = run_task(
                    FailedProcess(),
                    AgentConfig("secret"),
                    "runtime",
                    "Work",
                    output=Path(directory) / "result",
                )
                self.assertEqual(result["status"], expected)
                self.assertIsNone(result["archive"])

    def test_account_limit_rejects_task_before_submission(self):
        api = TaskService()
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(ValueError):
            run_task(
                api,
                AgentConfig("secret"),
                "runtime",
                "Work",
                output=Path(directory) / "result",
                timeout=3601,
            )
        self.assertEqual(api.submissions, [])

    def test_unconfirmed_cancellation_is_interrupted_and_never_resubmitted(self):
        from unittest.mock import patch

        import httpx
        from nebius_sandbox import SandboxClient

        requests = []

        def respond(request, **options):
            requests.append((request.method, str(request.url)))
            if request.method == "DELETE":
                raise httpx.ConnectError("cancellation connection lost")
            if "/operations/" in str(request.url):
                raise KeyboardInterrupt
            if str(request.url).endswith("/whoami"):
                payload = {
                    "token_uuid": "identity",
                    "token_expiration": None,
                    "limits": {"instance_max_timeout": 3600},
                }
            elif str(request.url).endswith("/files"):
                payload = {
                    "uuid": "uploaded",
                    "sha256": hashlib.sha256(request.content).hexdigest(),
                    "size": len(request.content),
                }
            else:
                payload = {"uuid": "known-job"}
            return httpx.Response(200, json=payload, request=request)

        with (
            tempfile.TemporaryDirectory() as directory,
            patch("httpx.HTTPTransport.handle_request", side_effect=respond),
            patch("httpx.AsyncHTTPTransport.handle_async_request", side_effect=respond),
            self.assertWarnsRegex(RuntimeWarning, "Cancellation unconfirmed: known-job"),
        ):
            result = run_task(
                SandboxClient("sandbox-secret", base_url="https://sandbox.example/v1"),
                AgentConfig("inference-secret"),
                "runtime",
                "Work",
                output=Path(directory) / "result",
            )
            self.assertEqual(result["status"], "interrupted")
            self.assertEqual(result["operation_id"], "known-job")
            saved = json.loads((Path(directory) / "result/job.json").read_text())
            self.assertEqual(saved["status"], "interrupted")
        self.assertEqual(
            sum(method == "POST" and url.endswith("/instances") for method, url in requests), 1
        )
        self.assertEqual(sum(method == "DELETE" for method, _ in requests), 1)

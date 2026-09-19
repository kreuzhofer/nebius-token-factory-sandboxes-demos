"""Exercise the sandbox wire contract and distinguish transport, operation, and process failure."""

import json
import os
import unittest
from unittest.mock import patch

import httpx
from http_transport import TransportError
from nebius_sandbox import ExecutionFailed, Operation, OperationFailed, SandboxClient

from tests.sandbox_http import sandbox_responses


class SandboxClientTests(unittest.TestCase):
    def setUp(self):
        self.client = SandboxClient("secret", "project", "https://sandbox.example/v1/")

    def test_submit_maps_generic_command_without_receipt_defaults(self):
        response = httpx.Response(200, json={"uuid": "job"})
        with patch("httpx.HTTPTransport.handle_request", return_value=response) as send:
            operation = self.client.submit(
                "image",
                command="/bin/sh",
                args=["-c", "echo ready"],
                cwd="/work",
                files={"/work/input": {"uuid": "file", "mode": "0400"}},
                env={"EXAMPLE": "value"},
                timeout=12,
            )
        request = send.call_args.args[0]
        self.assertEqual(operation, "job")
        self.assertEqual(str(request.url), "https://sandbox.example/v1/instances")
        self.assertEqual(request.headers["Authorization"], "Bearer secret")
        self.assertEqual(request.headers["Project"], "project")
        body = json.loads(request.content)
        self.assertEqual(body["command"], "/bin/sh")
        self.assertEqual(body["args"], ["-c", "echo ready"])
        self.assertEqual(body["cwd"], "/work")
        self.assertEqual(body["files"]["/work/input"]["uuid"], "file")
        self.assertEqual(body["files"]["/work/input"]["mode"], "0400")
        self.assertEqual(body["env"], {"EXAMPLE": "value"})
        self.assertEqual(body["timeout"], 12)
        self.assertFalse(body["preserve_env"])
        self.assertFalse(body["networking"]["enabled"])
        self.assertFalse(body["disposable"])
        self.assertEqual(body["resources_limits"]["max_layer_bytes"], 268435456)
        self.assertEqual(body["truncate_output_at"], 65536)
        self.assertNotIn("stdin", body)

    def test_submit_preserves_explicit_execution_settings(self):
        with sandbox_responses({"uuid": "job"}) as requests:
            self.client.submit(
                "tag:runtime",
                command="/usr/local/bin/python3",
                stdin="print('hello')",
                networking=True,
                disposable=True,
                max_layer_bytes=1024,
                output_limit=2048,
            )
        body = json.loads(requests[0].content)
        self.assertEqual(body["image"], "tag:runtime")
        self.assertEqual(
            body["stdin"], {"value": "print('hello')", "encoding": "ascii", "close": True}
        )
        self.assertTrue(body["networking"]["enabled"])
        self.assertTrue(body["disposable"])
        self.assertFalse(body["preserve_env"])
        self.assertEqual(body["resources_limits"]["max_layer_bytes"], 1024)
        self.assertEqual(body["truncate_output_at"], 2048)
        self.assertNotIn("cwd", body)

    def test_ambiguous_submission_is_not_retried_or_leaked(self):
        for failure in (
            httpx.ReadTimeout("secret"),
            httpx.ConnectError("secret"),
            httpx.Response(429, json={"error": "secret"}),
            httpx.Response(503, json={"error": "secret"}),
        ):
            with self.subTest(failure=type(failure).__name__):

                def respond(request, failure=failure):
                    if isinstance(failure, Exception):
                        raise failure
                    return failure

                with patch("httpx.HTTPTransport.handle_request", side_effect=respond) as send:
                    with self.assertRaises(TransportError) as raised:
                        self.client.submit("image", command="/bin/true")
                self.assertEqual(send.call_count, 1)
                self.assertNotIn("secret", str(raised.exception))

    def test_http_error_body_is_not_exposed(self):
        for call in (self.client.list_images, self.client.limits):
            with (
                self.subTest(call=call.__name__),
                sandbox_responses((401, {"error": "secret"})),
            ):
                with self.assertRaisesRegex(TransportError, "HTTP 401") as raised:
                    call()
                self.assertNotIn("secret", str(raised.exception))

    def test_image_import_listing_and_fallback_result(self):
        with sandbox_responses(
            {"uuid": "import"},
            {
                "images": [
                    {"uuid": "existing", "tag": "runtime", "created_at": "2026-09-19T00:00:00Z"}
                ]
            },
            {"status": "SUCCESS", "result": {"image": "imported"}},
        ) as requests:
            self.assertEqual(
                self.client.import_image("docker://example/image", timeout=15), "import"
            )
            images = self.client.list_images(limit=2, offset=3)
            self.assertEqual(images["images"][0]["uuid"], "existing")
            self.assertEqual(images["images"][0]["created_at"], "2026-09-19T00:00:00Z")
            self.assertEqual(self.client.wait("import", 5).image, "imported")
        self.assertEqual(
            json.loads(requests[0].content),
            {
                "registry": {"url": "docker://example/image"},
                "timeout": 15,
            },
        )
        self.assertEqual(dict(requests[1].url.params), {"limit": "2", "offset": "3"})
        self.assertEqual(requests[2].url.path, "/v1/operations/import")

    def test_poll_until_success(self):
        with (
            sandbox_responses({"status": "EXECUTING"}, {"status": "SUCCESS"}),
            patch("nebius_sandbox.time.sleep"),
        ):
            self.assertEqual(self.client.wait("id", 5).status, "SUCCESS")

    def test_unchecked_wait_preserves_failed_operation_and_reports_status_changes(self):
        statuses = []
        with (
            sandbox_responses(
                {"status": "EXECUTING"}, {"status": "EXECUTING"}, {"status": "FAILED"}
            ),
            patch("nebius_sandbox.time.sleep"),
        ):
            operation = self.client.wait("job", 5, check=False, on_status=statuses.append)
        self.assertEqual(operation.status, "FAILED")
        self.assertEqual(statuses, ["EXECUTING", "FAILED"])

    def test_unchecked_execution_exposes_timeout_without_calling_it_successful(self):
        operation = Operation.from_response(
            "job",
            {
                "status": "SUCCESS",
                "metadata": {
                    "result": {"state": {"exit_code": -1, "timed_out": True, "signal": 9}}
                },
            },
        )
        result = operation.execution_result(check=False)
        self.assertTrue(result.timed_out)
        self.assertEqual(result.signal, 9)
        self.assertFalse(result.successful)

    def test_limits_only_returns_limit_values(self):
        with sandbox_responses(
            {
                "token_uuid": "private-identity",
                "token_expiration": None,
                "limits": {"instance_max_timeout": 3600},
            }
        ) as requests:
            self.assertEqual(self.client.limits(), {"instance_max_timeout": 3600})
        self.assertEqual(requests[0].url.path, "/v1/whoami")
        self.assertEqual(requests[0].headers["Authorization"], "Bearer secret")
        self.assertEqual(requests[0].headers["Project"], "project")

    def test_explicit_configuration_is_not_reinterpreted_as_sdk_environment_names(self):
        with patch.dict(
            os.environ, {"literal-token": "wrong-token", "NEBIUS_PROJECT_ID": "wrong-project"}
        ):
            client = SandboxClient("literal-token", base_url="https://sandbox.example/custom/v1/")
            with sandbox_responses(
                {"token_uuid": "identity", "token_expiration": None},
                {"uuid": "job"},
            ) as requests:
                self.assertEqual(client.limits(), {})
                self.assertEqual(client.submit("image", command="/bin/true"), "job")
        for request in requests:
            self.assertEqual(request.headers["Authorization"], "Bearer literal-token")
            self.assertNotIn("Project", request.headers)
            self.assertTrue(request.url.path.startswith("/custom/v1/"))

    def test_download_preserves_binary_checkpoint_contents_and_encodes_path(self):
        image = "12345678-1234-1234-1234-123456789abc"
        with sandbox_responses(b"\x00\xffarchive") as requests:
            self.assertEqual(self.client.download(image, "/work/a b&c.tar"), b"\x00\xffarchive")
        self.assertEqual(requests[0].url.path, f"/v1/inspect/{image}/download")
        self.assertEqual(requests[0].url.params["path"], "/work/a b&c.tar")

    def test_import_completion_requires_a_successful_retained_image(self):
        for final, expected, error in (
            ({"status": "SUCCESS", "result": {"image": "ready"}}, "ready", None),
            ({"status": "SUCCESS"}, None, RuntimeError),
            ({"status": "FAILED", "result_image_uuid": "partial"}, None, OperationFailed),
        ):
            with (
                self.subTest(final=final),
                sandbox_responses({"uuid": "import-job"}, {"status": "PENDING"}, final) as requests,
                patch("nebius_sandbox.time.sleep"),
            ):
                if error:
                    with self.assertRaises(error):
                        self.client.import_image_and_wait("docker://example/runtime", timeout=20)
                else:
                    self.assertEqual(
                        self.client.import_image_and_wait("docker://example/runtime", timeout=20),
                        expected,
                    )
                self.assertEqual(json.loads(requests[0].content)["timeout"], 20)
                self.assertEqual(requests[-1].url.path, "/v1/operations/import-job")

    def test_import_wait_deadline_cancels_known_import(self):
        with sandbox_responses({"uuid": "import-job"}, b"") as requests:
            with self.assertRaises(TimeoutError):
                self.client.import_image_and_wait("docker://example/runtime", wait_timeout=-1)
        self.assertEqual(requests[-1].method, "DELETE")
        self.assertEqual(requests[-1].url.path, "/v1/operations/import-job")

    def test_deadline_cancels(self):
        with sandbox_responses(b"") as requests:
            with self.assertRaises(TimeoutError):
                self.client.wait("id", -1)
        self.assertEqual(
            [(r.method, r.url.path) for r in requests], [("DELETE", "/v1/operations/id")]
        )

    def test_interruption_cancels_but_transport_failure_leaves_known_job(self):
        for failure, expected in (
            (KeyboardInterrupt(), KeyboardInterrupt),
            (httpx.ReadError("secret"), TransportError),
        ):
            with (
                self.subTest(failure=type(failure).__name__),
                sandbox_responses(failure, b"") as requests,
            ):
                with self.assertRaises(expected):
                    self.client.wait("id", 5)
            self.assertEqual(
                [r.method for r in requests],
                ["GET", "DELETE"] if expected is KeyboardInterrupt else ["GET"],
            )

    def test_failed_operation_differs_from_failed_process(self):
        for status in ("FAILED", "CANCELLED"):
            with (
                self.subTest(status=status),
                sandbox_responses({"status": status}),
            ):
                with self.assertRaises(OperationFailed):
                    self.client.wait("job", 5)
        for state in (
            {"exit_code": 1},
            {"exit_code": 0, "timed_out": True},
            {"exit_code": 0, "signal": 9},
            {},
        ):
            operation = Operation.from_response(
                "job", {"status": "SUCCESS", "metadata": {"result": {"state": state}}}
            )
            with self.subTest(state=state), self.assertRaises(ExecutionFailed):
                operation.execution_result()

    def test_execution_decodes_streams_and_rejects_truncation(self):
        payload = {
            "kind": "instance",
            "status": "SUCCESS",
            "result_image_uuid": "retained",
            "metadata": {
                "command": "/bin/true",
                "image": "source",
                "result": {
                    "state": {"exit_code": 0},
                    "stdout": {"encoding": "base64", "value": "aGVsbG8="},
                    "stderr": {"encoding": "ascii", "value": "notice"},
                },
            },
        }
        with sandbox_responses(payload):
            result = self.client.get_operation("job").execution_result()
        self.assertEqual(
            (result.stdout, result.stderr, result.image), ("hello", "notice", "retained")
        )
        payload["metadata"]["result"]["stdout"]["truncated"] = True
        with sandbox_responses(payload), self.assertRaisesRegex(RuntimeError, "truncated"):
            self.client.get_operation("job").execution_result()

    def test_artifact_execution_requires_image_but_disposable_execution_does_not(self):
        payload = {"status": "SUCCESS", "metadata": {"result": {"state": {"exit_code": 0}}}}
        operation = Operation.from_response("job", payload)
        self.assertIsNone(operation.execution_result().image)
        with self.assertRaisesRegex(RuntimeError, "job.*no filesystem image"):
            operation.execution_result(require_image=True)
        payload["result_image_uuid"] = "retained"
        self.assertEqual(
            Operation.from_response("job", payload).execution_result(require_image=True).image,
            "retained",
        )
        payload["metadata"]["result"]["state"]["exit_code"] = 1
        with self.assertRaises(ExecutionFailed):
            Operation.from_response("job", payload).execution_result(require_image=True)

    def test_upload_rejects_checksum_mismatch(self):
        with sandbox_responses({"uuid": "file", "sha256": "wrong", "size": 8}):
            with self.assertRaisesRegex(RuntimeError, "checksum"):
                self.client.upload(b"contents")

    def test_upload_retains_content_and_file_mode(self):
        with sandbox_responses(
            {
                "uuid": "file",
                "size": 5,
                "sha256": "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
            }
        ) as requests:
            self.assertEqual(
                self.client.upload(b"hello", mode="0400"), {"uuid": "file", "mode": "0400"}
            )
        self.assertEqual(requests[0].content, b"hello")
        self.assertEqual(requests[0].headers["Content-Type"], "application/octet-stream")

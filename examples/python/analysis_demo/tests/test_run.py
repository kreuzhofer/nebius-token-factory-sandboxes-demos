"""Exercise continuation through the launcher against the sandbox service boundary."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from codingagent_demo import AgentConfig
from codingagent_demo.tests.test_run import TaskService
from http_transport import TransportError
from nebius_sandbox import Operation

from analysis_demo.run import run_analysis
from analysis_demo.tests.test_validation import (
    EXPECTED,
    archive_bytes,
    chart,
    initial_files,
)


class AnalysisService(TaskService):
    def __init__(self, failure=None):
        super().__init__()
        self.blobs = {}
        self.jobs = {}
        self.probes = []
        self.failure = failure
        self.reads = []

    def upload(self, content):
        if self.jobs and self.failure == "preparation_interrupt":
            raise KeyboardInterrupt
        ref = super().upload(content)
        self.blobs[ref["uuid"]] = content
        return ref

    def submit(self, image, **options):
        super().submit(image, **options)
        if "stdin" in options:
            self.probes.append(image)
            return "parent-probe"
        job = f"job-{len(self.submissions)}"
        request = (
            json.loads(self.blobs[options["files"]["/opt/coding-request.json"]["uuid"]])
            if "/opt/coding-request.json" in options["files"]
            else {"controlled_failure": True}
        )
        contract = json.loads(self.blobs[options["files"]["/workspace/analysis-step.json"]["uuid"]])
        step = contract["step_id"]
        inherited = dict(self.jobs["job-1"]["files"]) if len(self.jobs) else {}
        if not inherited:
            files = initial_files(step)
        else:
            previous = self.jobs["job-1"]["contract"]
            prior = f"steps/{previous['step_id']}/"
            summary = {key: EXPECTED[key] for key in ("count", "total", "regions")}
            summary.update(
                step_id=step,
                lineage=inherited[prior + "lineage.txt"].decode(),
                cleaned_sha256=hashlib.sha256(inherited[prior + "cleaned.csv"]).hexdigest(),
            )
            files = inherited | {
                f"steps/{step}/summary.json": json.dumps(summary).encode(),
                f"steps/{step}/chart.png": chart(
                    step, {k: v["total"] for k, v in EXPECTED["regions"].items()}
                ),
            }
        self.jobs[job] = {"request": request, "contract": contract, "files": files}
        return job

    def wait(self, operation, seconds, **options):
        if operation == "job-2":
            if self.failure == "disconnected":
                raise TransportError("connection lost")
            if self.failure == "interrupt":
                raise KeyboardInterrupt
            if self.failure == "cancelled":
                return Operation.from_response(operation, {"status": "CANCELLED"})
        return Operation.from_response(
            operation,
            {
                "status": "SUCCESS",
                "result_image_uuid": operation + "-image",
                "metadata": {
                    "result": {
                        "state": {
                            "exit_code": 1
                            if operation == "job-2" and self.failure == "failed"
                            else 0,
                            "timed_out": operation == "job-2" and self.failure == "timed_out",
                        }
                    }
                },
            },
        )

    def download(self, image, path):
        self.reads.append((image, path))
        job = self.jobs[image.removesuffix("-image")]
        if path.endswith("workspace.tar.gz"):
            files = dict(job["files"])
            prefix = "steps/" + job["contract"]["step_id"] + "/"
            if image == "job-2-image":
                if self.failure == "missing":
                    files.pop(prefix + "summary.json")
                elif self.failure == "invalid":
                    files[prefix + "chart.png"] = b"not a chart"
                elif self.failure == "stale":
                    files = self.jobs["job-1"]["files"]
            return archive_bytes(files)
        if path.endswith("result.json"):
            return json.dumps(
                {
                    "status": "failed" if job["request"].get("controlled_failure") else "completed",
                    "answer": "Analysis complete",
                    "error": None,
                }
            ).encode()
        return b"diagnostic\n"


class AnalysisTests(unittest.TestCase):
    def test_followup_inherits_checkpoint_and_receives_saved_context_without_original_uploads(self):
        service = AnalysisService()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "analysis"
            record = run_analysis(service, AgentConfig("key"), "runtime", root)
            self.assertTrue(record["passed"], record)
            first, second = record["steps"]
            self.assertEqual(second["parent_checkpoint"], first["execution"]["image"])
            self.assertEqual(service.submissions[1][0], "job-1-image")
            second_uploads = service.submissions[1][1]["files"]
            self.assertNotIn("/workspace/sales.csv", second_uploads)
            context = json.loads(
                service.blobs[second_uploads["/workspace/analysis-context.json"]["uuid"]]
            )
            self.assertEqual(context["request"], first["request"])
            self.assertEqual(context["answer"], first["execution"]["answer"])
            self.assertEqual(context["artifacts"], first["validation"]["artifacts"])
            self.assertIn("analysis-context.json", service.jobs["job-2"]["request"]["task"])
            self.assertEqual(record["last_successful_result"]["checkpoint"], "job-2-image")
            self.assertTrue(record["parent_verification"]["passed"])
            self.assertEqual(len(service.submissions), 3)
            self.assertEqual(service.probes, ["job-1-image"])
            self.assertEqual(record["parent_verification"]["operation_id"], "parent-probe")
            self.assertEqual(json.loads((root / "analysis.json").read_text())["passed"], True)

    def test_followup_failures_preserve_initial_success_and_never_resubmit(self):
        for failure, status in [
            ("failed", "failed"),
            ("timed_out", "timed_out"),
            ("cancelled", "cancelled"),
            ("disconnected", "interrupted"),
            ("interrupt", "interrupted"),
            ("preparation_interrupt", "interrupted"),
            ("missing", "completed"),
            ("invalid", "completed"),
            ("stale", "completed"),
        ]:
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                service = AnalysisService(failure)
                record = run_analysis(
                    service, AgentConfig("key"), "runtime", Path(directory) / "run"
                )
                self.assertFalse(record["passed"])
                self.assertEqual(record["last_successful_result"]["checkpoint"], "job-1-image")
                attempt = record["steps"][1]
                self.assertEqual(attempt["execution"]["status"], status)
                self.assertFalse(attempt["validation"]["passed"])
                self.assertTrue(record["parent_verification"]["passed"])
                self.assertEqual(
                    len(service.submissions), 2 if failure == "preparation_interrupt" else 3
                )
                if failure != "preparation_interrupt":
                    self.assertEqual(attempt["execution"]["operation_id"], "job-2")

    def test_controlled_failed_child_keeps_initial_result_and_checkpoint_usable(self):
        with tempfile.TemporaryDirectory() as directory:
            service = AnalysisService()
            record = run_analysis(
                service,
                AgentConfig("key"),
                "runtime",
                Path(directory) / "run",
                fail_followup=True,
            )
            self.assertFalse(record["passed"])
            self.assertEqual(record["steps"][1]["execution"]["status"], "failed")
            self.assertEqual(record["last_successful_result"]["checkpoint"], "job-1-image")
            self.assertTrue(record["parent_verification"]["passed"])
            self.assertFalse(service.submissions[1][1]["networking"])
            self.assertIsNone(service.submissions[1][1]["env"])

    def test_unconfirmed_parent_probe_cannot_report_overall_success(self):
        for failure in (TransportError("connection lost"), TimeoutError(), KeyboardInterrupt()):

            class ProbeFailure(AnalysisService):
                def wait(self, operation, seconds, **options):
                    if operation == "parent-probe":
                        assert isinstance(self.failure, BaseException)
                        raise self.failure
                    return super().wait(operation, seconds, **options)

            with (
                self.subTest(failure=type(failure).__name__),
                tempfile.TemporaryDirectory() as directory,
            ):
                record = run_analysis(
                    ProbeFailure(failure),
                    AgentConfig("key"),
                    "runtime",
                    Path(directory) / "run",
                )
                self.assertFalse(record["passed"])
                self.assertFalse(record["parent_verification"]["passed"])
                self.assertIn(type(failure).__name__, record["parent_verification"]["error"])
                self.assertEqual(record["last_successful_result"]["checkpoint"], "job-2-image")

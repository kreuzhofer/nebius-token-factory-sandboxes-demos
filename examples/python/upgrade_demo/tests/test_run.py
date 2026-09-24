"""Exercise upgrade selection through the external sandbox service boundary."""

import json
import tempfile
import unittest
from pathlib import Path

from codingagent_demo import AgentConfig
from codingagent_demo.tests.test_run import TaskService
from http_transport import TransportError
from nebius_sandbox import Operation

from upgrade_demo.run import run_demo


class UpgradeService(TaskService):
    def __init__(self, failure=None):
        super().__init__()
        self.jobs = {}
        self.agent_parents = []
        self.failure = failure
        self.baseline_checks = 0

    def submit(self, image, **options):
        super().submit(image, **options)
        job = f"job-{len(self.submissions)}"
        script = options["args"][1]
        self.jobs[job] = (image, script, options)
        if script == "/opt/coding-worker.py":
            self.agent_parents.append(image)
        return job

    def wait(self, operation, seconds, **options):
        parent, script, submitted = self.jobs[operation]
        if script == "/opt/coding-worker.py" and self.failure == "agent_disconnected":
            raise TransportError("connection lost")
        if script.endswith("check_ledger.py") and parent == "job-1-image":
            self.baseline_checks += 1
            if self.baseline_checks > 1 and self.failure == "baseline_timeout":
                raise TimeoutError()
        passed = not (script.endswith("check_ledger.py") and parent.endswith("-regression"))
        if (
            script.endswith("check_ledger.py")
            and parent != "job-1-image"
            and self.failure == "agent_invalid"
        ):
            passed = False
        check_status = "passed" if passed else "failed"
        if self.baseline_checks > 1 and parent == "job-1-image":
            if self.failure in {"baseline_checker_timeout", "baseline_test_failure"}:
                passed = False
                check_status = (
                    "incomplete" if self.failure == "baseline_checker_timeout" else "failed"
                )
        image = None if submitted["disposable"] else operation + "-image"
        if script.endswith("inject.py"):
            image = operation + "-regression"
        return Operation.from_response(
            operation,
            {
                "status": "SUCCESS",
                "result_image_uuid": image,
                "metadata": {
                    "result": {
                        "state": {"exit_code": 0 if passed else 1},
                        "stdout": {
                            "value": json.dumps(
                                {
                                    "passed": passed,
                                    "status": check_status,
                                    "version": "2.0.36",
                                    "error": None if passed else "Injected regression",
                                }
                            )
                        },
                    }
                },
            },
        )

    def download(self, image, path):
        if path.endswith("baseline.json"):
            return b'{"ledger.py":"baseline-code-hash"}'
        return super().download(image, path)


class UpgradeTests(unittest.TestCase):
    def test_passing_upgrade_returns_validated_candidate_and_project_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            service = UpgradeService()
            record = run_demo(
                service,
                AgentConfig("key"),
                "runtime",
                Path(directory) / "run",
                scenarios=("passing",),
            )
            self.assertTrue(record["demonstration_complete"], record)
            self.assertEqual(record["exit_code"], 0)
            scenario = record["scenarios"][0]
            self.assertEqual(scenario["outcome"], "upgrade_accepted")
            self.assertEqual(scenario["selected_result"]["checkpoint"], scenario["agent"]["image"])
            self.assertTrue(Path(scenario["selected_result"]["archive"]).is_file())
            self.assertEqual(service.agent_parents, [record["baseline"]["image"]])
            saved = json.loads((Path(directory) / "run" / "upgrade.json").read_text())
            self.assertEqual(saved, record)

    def test_controlled_regression_returns_unchanged_baseline_from_independent_branch(self):
        with tempfile.TemporaryDirectory() as directory:
            service = UpgradeService()
            record = run_demo(
                service,
                AgentConfig("key"),
                "runtime",
                Path(directory) / "run",
                scenarios=("passing", "rollback"),
            )
            self.assertTrue(record["demonstration_complete"], record)
            self.assertEqual(record["exit_code"], 1)
            passing, rollback = record["scenarios"]
            baseline = record["baseline"]
            self.assertEqual(service.agent_parents, [baseline["image"], baseline["image"]])
            self.assertEqual(passing["outcome"], "upgrade_accepted")
            self.assertTrue(rollback["validation"]["passed"])
            self.assertFalse(rollback["injected_validation"]["passed"])
            self.assertTrue(rollback["baseline_verification"]["passed"])
            self.assertEqual(rollback["outcome"], "upgrade_rejected_baseline_verified")
            self.assertEqual(
                rollback["selected_result"],
                {"checkpoint": baseline["image"], "archive": baseline["archive"], "verified": True},
            )
            checks = [
                options
                for _, script, options in service.jobs.values()
                if script.endswith("check_ledger.py")
            ]
            self.assertTrue(all(c["disposable"] and not c["networking"] for c in checks))

    def test_unconfirmed_agent_stops_further_tasks_and_preserves_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            service = UpgradeService("agent_disconnected")
            record = run_demo(service, AgentConfig("key"), "runtime", Path(directory) / "run")
            self.assertFalse(record["demonstration_complete"])
            self.assertEqual(len(service.agent_parents), 1)
            attempt = record["scenarios"][0]
            self.assertEqual(attempt["agent"]["status"], "interrupted")
            self.assertTrue(attempt["selected_result"]["verified"])
            self.assertEqual(attempt["selected_result"]["checkpoint"], record["baseline"]["image"])

    def test_failed_upgrade_before_injection_is_not_controlled_rollback_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            record = run_demo(
                UpgradeService("agent_invalid"),
                AgentConfig("key"),
                "runtime",
                Path(directory) / "run",
                scenarios=("rollback",),
            )
            attempt = record["scenarios"][0]
            self.assertIsNone(attempt["injection"])
            self.assertFalse(record["demonstration_complete"])
            self.assertEqual(attempt["outcome"], "upgrade_rejected_baseline_verified")

    def test_incomplete_baseline_check_retains_reference_without_claiming_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            record = run_demo(
                UpgradeService("baseline_timeout"),
                AgentConfig("key"),
                "runtime",
                Path(directory) / "run",
                scenarios=("rollback",),
            )
            attempt = record["scenarios"][0]
            self.assertFalse(record["demonstration_complete"])
            self.assertEqual(attempt["outcome"], "upgrade_rejected_baseline_unverified")
            self.assertFalse(attempt["selected_result"]["verified"])
            self.assertIsNotNone(attempt["baseline_verification"]["operation_id"])

    def test_checker_timeout_and_failed_baseline_tests_have_distinct_outcomes(self):
        for failure, outcome in (
            ("baseline_checker_timeout", "upgrade_rejected_baseline_unverified"),
            ("baseline_test_failure", "upgrade_rejected_baseline_failed"),
        ):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                record = run_demo(
                    UpgradeService(failure),
                    AgentConfig("key"),
                    "runtime",
                    Path(directory) / "run",
                    scenarios=("rollback",),
                )
                self.assertEqual(record["scenarios"][0]["outcome"], outcome)
                self.assertFalse(record["demonstration_complete"])

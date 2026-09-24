"""Exercise trusted checks through the ledger and validator command interfaces."""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

DEMO = Path(__file__).resolve().parents[1]


class FixtureTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.workspace = Path(self.directory.name)
        shutil.copyfile(DEMO / "reference" / "ledger.py", self.workspace / "ledger.py")
        (self.workspace / "requirements.txt").write_text("SQLAlchemy==2.0.36\n")
        self.ledger("init", "ledger.db")
        for category, cents in (("food", 1200), ("food", -200), ("travel", 500)):
            self.ledger("add", "ledger.db", category, str(cents))

    def ledger(self, *args):
        return subprocess.run(
            [sys.executable, str(self.workspace / "ledger.py"), *args],
            cwd=self.workspace,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )

    def validate(self, *extra):
        result = subprocess.run(
            [
                sys.executable,
                str(DEMO / "checks" / "check_ledger.py"),
                "--workspace",
                str(self.workspace),
                "--version",
                "2.0.36",
                *extra,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result, json.loads(result.stdout)

    def test_upgraded_ledger_preserves_data_and_persists_new_transactions(self):
        result, report = self.validate()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(report["passed"])
        self.assertEqual(report["version"], "2.0.36")
        self.assertEqual(
            json.loads(self.ledger("report", "ledger.db").stdout),
            {"categories": {"food": 1000, "travel": 500}, "total": 1500, "count": 3},
        )

    def test_uncommitted_transactions_cannot_pass_even_when_agent_claims_success(self):
        ledger = self.workspace / "ledger.py"
        ledger.write_text(ledger.read_text().replace("engine.begin()", "engine.connect()"))
        (self.workspace / "check_ledger.py").write_text("print('all tests passed')\n")
        result, report = self.validate()
        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["passed"])
        self.assertIn("not persisted", report["error"])

    def test_wrong_project_pin_and_lost_inherited_entries_are_rejected(self):
        (self.workspace / "requirements.txt").write_text("SQLAlchemy==1.4.54\n")
        result, report = self.validate()
        self.assertEqual(result.returncode, 1)
        self.assertIn("dependency pin", report["error"])
        (self.workspace / "requirements.txt").write_text("SQLAlchemy==2.0.36\n")
        self.ledger("add", "ledger.db", "unexpected", "999")
        result, report = self.validate()
        self.assertEqual(result.returncode, 1)
        self.assertIn("Pre-existing ledger entries changed", report["error"])

    def test_baseline_proof_detects_new_candidate_files(self):
        import hashlib

        manifest = self.workspace.parent / (self.workspace.name + "-manifest.json")
        self.addCleanup(manifest.unlink, missing_ok=True)
        manifest.write_text(
            json.dumps(
                {
                    p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in self.workspace.iterdir()
                }
            )
        )
        result, report = self.validate("--baseline-manifest", str(manifest))
        self.assertEqual(result.returncode, 0, report)
        (self.workspace / "injected-regression.txt").write_text("candidate only")
        result, report = self.validate("--baseline-manifest", str(manifest))
        self.assertEqual(result.returncode, 1)
        self.assertIn("candidate files appeared", report["error"])

    def test_ledger_subprocess_timeout_is_incomplete_verification(self):
        (self.workspace / "ledger.py").write_text("import time\ntime.sleep(60)\n")
        result, report = self.validate()
        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["passed"])
        self.assertEqual(report["status"], "incomplete")
        self.assertIn("TimeoutExpired", report["error"])

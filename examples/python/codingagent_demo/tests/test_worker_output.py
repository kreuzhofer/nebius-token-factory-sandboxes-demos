"""Observe the worker's process output without installing or invoking a model."""

import os
import select
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


class WorkerOutputTests(unittest.TestCase):
    def test_progress_is_published_before_exit_and_split_credentials_are_redacted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "logs").mkdir()
            child = root / "agent.py"
            child.write_text(
                "import os,sys,time\nfrom pathlib import Path\n"
                "key=os.environ['TEST_KEY']\n"
                "os.write(1, b'progress ' + key[:5].encode()); time.sleep(.05)\n"
                "os.write(1, key[5:].encode() + b'\\n')\n"
                "os.write(2, key[:3].encode()); time.sleep(.05)\n"
                "os.write(2, key[3:].encode() + b'\\n')\n"
                f"while not Path({str(root / 'finish')!r}).exists(): time.sleep(.01)\n"
                "print('done', flush=True)\n"
            )
            harness = (
                "import os,sys\nfrom codingagent_demo.worker import run_agent\n"
                f"run_agent([sys.executable, '-u', {str(child)!r}], cwd={str(root)!r}, "
                f"env=dict(os.environ), output={str(root / 'logs')!r}, key=os.environ['TEST_KEY'], "
                "timeout=5, live=True)"
            )
            process = subprocess.Popen(
                [sys.executable, "-u", "-c", harness],
                cwd=Path(__file__).resolve().parents[2],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=dict(os.environ, TEST_KEY="test-secret-12345"),
            )
            assert process.stdout is not None
            try:
                received = b""
                until = time.monotonic() + 4
                while b"\n" not in received and time.monotonic() < until:
                    if select.select([process.stdout], [], [], 0.1)[0]:
                        chunk = os.read(process.stdout.fileno(), 4096)
                        if not chunk:
                            break
                        received += chunk
                self.assertIn(b"progress [REDACTED]\n", received)
                self.assertIsNone(
                    process.poll(), "Output must arrive while the task is still running"
                )
                (root / "finish").touch()
                rest, errors = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 0, errors)
                self.assertNotIn(b"test-secret-12345", received + rest + errors)
                self.assertEqual(
                    (root / "logs/events.jsonl").read_text(), "progress [REDACTED]\ndone\n"
                )
                self.assertEqual((root / "logs/stderr.log").read_text(), "[REDACTED]\n")
            finally:
                process.kill() if process.poll() is None else None
                process.communicate()

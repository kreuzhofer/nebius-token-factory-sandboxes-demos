"""Exercise actual worker file handoffs and the uploaded Python package."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel

from receipt_demo.__main__ import inputs_for_run
from receipt_demo.execution import run
from receipt_demo.sandbox import upload_code
from receipt_demo.tests.test_receipts import tool_model

ROOT = Path(__file__).resolve().parents[2]


class WorkerExecutionTests(unittest.IsolatedAsyncioTestCase):
    def models(self):
        return SimpleNamespace(
            agent=FunctionModel(tool_model),
            vision=TestModel(
                custom_output_text=json.dumps(
                    {"pages": [{"number": 1, "text": "TOTAL USD 14.75", "blocks": []}]}
                )
            ),
            agent_name="test-agent",
            vision_name="test-vision",
            close=AsyncMock(),
        )

    async def execute(self, root):
        models = self.models()
        factory = Mock(return_value=models)
        with patch.dict(os.environ, {"NEBIUS_API_KEY": "inference-only"}, clear=True):
            result = await run(root, model_factory=factory)
            self.assertNotIn("NEBIUS_API_KEY", os.environ)
        self.assertEqual(factory.call_args.args[0].key, "inference-only")
        models.close.assert_awaited_once()
        self.assertEqual(json.loads((root / "output/result.json").read_text()), result)
        return result

    async def test_receipt_then_report_uses_portable_results_and_originals(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            records, sources = [], []
            for key, path, credit in inputs_for_run([], "minimal"):
                if key == "s04":
                    continue
                source = {
                    "receipt_id": key,
                    "source": path.name,
                    "path": str(path),
                    "credit": credit,
                }
                sources.append(source)
                (root / "workflow.json").write_text(json.dumps({"role": "receipt"}))
                (root / "inputs.json").write_text(json.dumps([source]))
                result = await self.execute(root)
                self.assertEqual(result["status"], "completed")
                self.assertEqual(result["receipt"]["rendered_pages"], [])
                records.append(result["receipt"])
            (root / "workflow.json").write_text(json.dumps({"role": "report"}))
            (root / "inputs.json").write_text(json.dumps(sources))
            (root / "receipts.json").write_text(json.dumps(records))
            result = await self.execute(root)
            self.assertEqual(result["status"], "completed")
            report = json.loads((root / "output/report.json").read_text())
            self.assertEqual(report["totals"], {"USD": "14.75"})
            self.assertEqual([e["outcome"] for e in report["entries"]], ["included", "error"])
            self.assertTrue((root / "output/report.pdf").read_bytes().startswith(b"%PDF-"))

    async def test_missing_original_for_successful_receipt_fails_report_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "workflow.json").write_text('{"role":"report"}')
            (root / "inputs.json").write_text(
                json.dumps([{"receipt_id": "a", "path": str(root / "missing.png")}])
            )
            (root / "receipts.json").write_text('[{"receipt_id":"a","source":"missing.png"}]')
            result = await self.execute(root)
            self.assertEqual(result["status"], "failed")
            self.assertNotIn("artifacts", result)


class PackagingTests(unittest.TestCase):
    def test_uploaded_files_import_without_demo_entrypoints(self):
        blobs = {}

        def upload(data):
            key = str(len(blobs))
            blobs[key] = data
            return {"uuid": key, "mode": "0600"}

        files = upload_code(SimpleNamespace(upload=upload), ROOT)
        self.assertIn("/app/requirements.txt", files)
        self.assertIn(
            b"-r ../requirements.txt", blobs[files["/app/receipt_demo/requirements.txt"]["uuid"]]
        )
        self.assertNotIn("/app/demo.py", files)
        self.assertNotIn("/app/receipt_demo/__main__.py", files)
        self.assertNotIn("/app/sandbox_jobs.py", files)
        with tempfile.TemporaryDirectory() as directory:
            for remote, ref in files.items():
                target = Path(directory) / Path(remote).relative_to("/app")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(blobs[ref["uuid"]])
            result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-c",
                    "import sys; sys.path.insert(0, sys.argv[1]); import receipt_demo.execution; import receipt_demo.orchestration",
                    directory,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

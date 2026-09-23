"""Independent sales expectations and bounded artifact validation."""

import gzip
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw, PngImagePlugin

from analysis_demo.validation import expected_sales, validate_artifacts

FIXTURE = Path(__file__).parents[1] / "fixtures"
EXPECTED = json.loads((FIXTURE / "expected.json").read_text())
CLEANED = b"sale_id,region,amount\nS001,North,100.00\nS002,North,25.50\nS003,South,80.00\nS004,South,-5.50\nS005,East,20.00\nS006,West,-5.00\n"


def chart(step, data):
    image = Image.new("RGB", (320, 200), "white")
    ImageDraw.Draw(image).rectangle((40, 40, 100, 160), fill="blue")
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("StepId", step)
    metadata.add_text("Data", json.dumps(data, sort_keys=True))
    output = io.BytesIO()
    image.save(output, format="PNG", pnginfo=metadata)
    return output.getvalue()


def archive_bytes(files):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        for name, content in files.items():
            member = tarfile.TarInfo("workspace/" + name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
    return gzip.compress(output.getvalue(), mtime=0)


def initial_files(step="initial-123"):
    summary = {key: EXPECTED[key] for key in ("count", "total", "discarded")}
    summary["step_id"] = step
    return {
        f"steps/{step}/summary.json": json.dumps(summary).encode(),
        f"steps/{step}/chart.png": chart(step, {"Total": "215.00"}),
        f"steps/{step}/cleaned.csv": CLEANED,
        f"steps/{step}/lineage.txt": b"created-in-the-initial-task\n",
    }


class ValidationTests(unittest.TestCase):
    def test_supplied_fixture_and_initial_outputs_match_independent_expected_values(self):
        self.assertEqual(expected_sales([FIXTURE / "sales.csv"]), EXPECTED)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "workspace.tar.gz"
            archive.write_bytes(archive_bytes(initial_files()))
            result = validate_artifacts(archive, "initial-123", EXPECTED, root / "artifacts")
            self.assertTrue(result["passed"], result)
            self.assertEqual(result["summary"]["total"], "215.00")
            self.assertEqual(result["lineage"], "created-in-the-initial-task\n")
            self.assertTrue((root / "artifacts/chart.png").is_file())

    def test_custom_files_clean_in_filename_order_and_keep_first_valid_id(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.csv").write_text("sale_id,region,amount\nS1, north ,1.25\nS2,East,bad\n")
            (root / "b.csv").write_text(
                "sale_id,region,amount\nS1,South,999.00\nS2,south,2.00\nS3,south,-0.25\n"
            )
            result = expected_sales([root / "b.csv", root / "a.csv"])
            self.assertEqual(result["count"], 3)
            self.assertEqual(result["discarded"], 2)
            self.assertEqual(result["total"], "3.00")
            self.assertEqual(
                result["regions"],
                {
                    "North": {"count": 1, "total": "1.25"},
                    "South": {"count": 2, "total": "1.75"},
                },
            )

    def test_wrong_totals_stale_chart_and_missing_lineage_do_not_validate(self):
        for case in ("wrong_total", "stale_chart", "missing_lineage", "wrong_cleaned"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                files = initial_files()
                prefix = "steps/initial-123/"
                if case == "wrong_total":
                    summary = json.loads(files[prefix + "summary.json"])
                    summary["total"] = "999.00"
                    files[prefix + "summary.json"] = json.dumps(summary).encode()
                elif case == "stale_chart":
                    files[prefix + "chart.png"] = chart("old-step", {"Total": "215.00"})
                elif case == "missing_lineage":
                    files.pop(prefix + "lineage.txt")
                else:
                    files[prefix + "cleaned.csv"] = CLEANED.replace(b"100.00", b"999.00")
                archive = root / "workspace.tar.gz"
                archive.write_bytes(archive_bytes(files))
                result = validate_artifacts(archive, "initial-123", EXPECTED, root / "artifacts")
                self.assertFalse(result["passed"], result)
                self.assertIsNotNone(result["error"])

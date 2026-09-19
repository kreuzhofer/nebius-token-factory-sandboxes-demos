"""Workflow checks use local source files and fake models; never live inference."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pypdfium2 as pdfium
from nebius_sandbox import SandboxClient
from pydantic import ValidationError
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel

from receipt_demo.__main__ import FIXTURES, inputs_for_run
from receipt_demo.agents import ReceiptAgent, ReportAgent
from receipt_demo.artifacts import retrieve_result
from receipt_demo.documents import prepare
from receipt_demo.models import Decisions, DuplicateGroup, Receipt
from receipt_demo.reconcile import reconcile
from receipt_demo.sandbox import submit_worker


def receipt(key, total="10.00", currency="EUR", **kwargs):
    return Receipt(
        receipt_id=key,
        source=key + ".png",
        total=total,
        currency=currency,
        transaction_type=kwargs.pop("transaction_type", "purchase"),
        **kwargs,
    )


def arithmetic(basis, *components):
    return {
        "basis": basis,
        "complete": True,
        "components": [
            {"role": role, "amount": amount, "page": 1, "text": f"{role}: {amount}"}
            for role, amount in components
        ],
    }


class AccountingTests(unittest.TestCase):
    def test_extracted_total_is_not_added_to_subtotal_and_tax(self):
        # The live control receipt returned all three amounts as summable components.
        parsed = receipt(
            "control",
            "14.75",
            "USD",
            comparable_components=["13.50", "1.25", "14.75"],
            components_complete=True,
            arithmetic={
                "basis": "subtotal",
                "complete": True,
                "components": [
                    {"role": "subtotal", "amount": "13.50", "page": 1, "text": "Subtotal 13.50"},
                    {"role": "tax_added", "amount": "1.25", "page": 1, "text": "Sales tax 1.25"},
                    {"role": "total", "amount": "14.75", "page": 1, "text": "TOTAL USD 14.75"},
                ],
            },
        )
        report = reconcile([parsed], Decisions())
        self.assertEqual(report.entries[0].outcome, "included")
        self.assertEqual(report.totals, {"USD": "14.75"})

    def test_signed_refund_discount_included_tax_and_separate_currencies(self):
        report = reconcile(
            [
                receipt(
                    "purchase", "22.60", taxes=[{"description": "included VAT", "amount": "2.60"}]
                ),
                receipt("refund", "9.50", transaction_type="refund"),
                receipt(
                    "discount",
                    "25.00",
                    arithmetic=arithmetic("subtotal", ("subtotal", "30"), ("discount", "-5")),
                ),
                receipt("usd", "14.75", "USD"),
            ],
            Decisions(),
        )
        self.assertEqual(report.totals, {"EUR": "38.10", "USD": "14.75"})

    def test_unknown_critical_fields_and_contradiction_are_excluded(self):
        report = reconcile(
            [
                receipt("total", None),
                receipt("currency", currency=None),
                receipt("direction", transaction_type=None),
                receipt(
                    "contradiction",
                    "25",
                    arithmetic=arithmetic("items", ("item", "12"), ("item", "8"), ("total", "25")),
                ),
                receipt("secondary", merchant=None, issues=["Unknown merchant"]),
            ],
            Decisions(),
        )
        self.assertEqual([e.outcome for e in report.entries], ["flagged"] * 4 + ["included"])
        self.assertEqual(report.totals, {"EUR": "10.00"})
        self.assertIsNone(report.entries[0].signed_amount)

    def test_amount_roles_exclude_included_tax_tender_change_and_carry_forward(self):
        report = reconcile(
            [
                receipt(
                    "multi",
                    "30",
                    arithmetic=arithmetic(
                        "items",
                        ("item", "12"),
                        ("item", "8"),
                        ("carry_forward", "20"),
                        ("carry_forward", "20"),
                        ("item", "6"),
                        ("item", "4"),
                        ("total", "30"),
                    ),
                ),
                receipt(
                    "tax",
                    "22.60",
                    arithmetic=arithmetic(
                        "items",
                        ("item", "10.70"),
                        ("item", "11.90"),
                        ("tax_included", "0.70"),
                        ("tax_included", "1.90"),
                        ("total", "22.60"),
                    ),
                ),
                receipt(
                    "cash",
                    "34.09",
                    arithmetic=arithmetic(
                        "subtotal",
                        ("subtotal", "34.97"),
                        ("discount", "0.88"),
                        ("tender", "50.09"),
                        ("change", "16.00"),
                        ("total", "34.09"),
                    ),
                ),
            ],
            Decisions(),
        )
        self.assertEqual([e.outcome for e in report.entries], ["included"] * 3)
        self.assertEqual(report.totals, {"EUR": "86.69"})

    def test_confirmed_bytes_pixels_and_suspected_duplicates(self):
        records = [
            receipt("a", content_hash="a", pixel_hash="pixels"),
            receipt("b", content_hash="a", pixel_hash="pixels"),
            receipt("c", content_hash="c", pixel_hash="pixels"),
            receipt("d"),
            receipt("e"),
        ]
        report = reconcile(
            records,
            Decisions(
                duplicates=[
                    DuplicateGroup(
                        receipt_ids=["d", "e"],
                        confidence="suspected",
                        reason="Matching transaction details; ID unreadable",
                    )
                ]
            ),
        )
        self.assertEqual(
            [e.outcome for e in report.entries],
            ["included", "duplicate", "duplicate", "flagged", "flagged"],
        )
        self.assertEqual(report.totals, {"EUR": "10.00"})
        self.assertEqual(report.entries[2].duplicate_of, "a")

    def test_suspected_duplicate_of_exact_copy_also_excludes_original(self):
        report = reconcile(
            [receipt("a", content_hash="same"), receipt("copy", content_hash="same"), receipt("b")],
            Decisions(
                duplicates=[
                    DuplicateGroup(
                        receipt_ids=["copy", "b"],
                        confidence="suspected",
                        reason="Uncertain identity",
                    )
                ]
            ),
        )
        self.assertEqual(report.totals, {})
        self.assertEqual(
            [entry.outcome for entry in report.entries], ["flagged", "duplicate", "flagged"]
        )

    def test_conflicting_model_duplicate_claim_cannot_confirm_a_match(self):
        report = reconcile(
            [receipt("eur", merchant="Shop A"), receipt("usd", currency="USD", merchant="Shop B")],
            Decisions(
                duplicates=[
                    DuplicateGroup(
                        receipt_ids=["eur", "usd"], confidence="confirmed", reason="Suggested match"
                    )
                ]
            ),
        )
        self.assertEqual([entry.outcome for entry in report.entries], ["flagged", "flagged"])
        self.assertTrue(all(entry.duplicate_of is None for entry in report.entries))
        self.assertEqual(report.totals, {})

    def test_all_excluded_does_not_invent_currency_zero(self):
        report = reconcile(
            [receipt("unknown", None), receipt("corrupt", error="Cannot render")], Decisions()
        )
        self.assertEqual(report.totals, {})
        self.assertEqual(report.status, "completed_with_flags")

    def test_bad_model_decisions_and_nonfinite_money_are_rejected(self):
        with self.assertRaises(ValueError):
            reconcile(
                [receipt("a")],
                Decisions(
                    duplicates=[
                        DuplicateGroup(
                            receipt_ids=["a", "missing"], confidence="confirmed", reason="same"
                        )
                    ]
                ),
            )
        for value in ("NaN", "Infinity", "1,23"):
            with self.assertRaises(ValidationError):
                receipt("a", value)


class DocumentTests(unittest.TestCase):
    def test_render_multipage_and_compare_reencoded_pixels(self):
        root = FIXTURES / "inputs"
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            pages, _, _ = prepare(root / "s01-en-multipage.pdf", temp / "pdf")
            self.assertEqual(len(pages), 2)
            _, raw_a, pixels_a = prepare(root / "s08-control.png", temp / "a")
            _, raw_b, pixels_b = prepare(root / "v02-control-reencoded.png", temp / "b")
            self.assertNotEqual(raw_a, raw_b)
            self.assertEqual(pixels_a, pixels_b)

    def test_fixture_selection_does_not_return_hidden_annotations(self):
        inputs = inputs_for_run([], "minimal")
        self.assertEqual([item[0] for item in inputs], ["s08", "s04", "v05"])
        self.assertTrue(all(len(item) == 3 for item in inputs))


def tool_model(messages, info):
    name = info.output_tools[0].name
    if "currency" in info.output_tools[0].parameters_json_schema.get("properties", {}):
        return ModelResponse(
            parts=[
                ToolCallPart(
                    name,
                    {
                        "merchant": "Fixture Cafe",
                        "currency": "USD",
                        "transaction_type": "purchase",
                        "printed_total": "14.75",
                        "total": "14.75",
                        "arithmetic": arithmetic(
                            "subtotal",
                            ("subtotal", "13.50"),
                            ("tax_added", "1.25"),
                            ("total", "14.75"),
                        ),
                    },
                )
            ]
        )
    return ModelResponse(parts=[ToolCallPart(name, {})])


class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_duplicated_extracted_page_gets_validation_retry(self):
        calls = []

        def vision(messages, info):
            calls.append(messages)
            pages = [{"number": 9, "text": "TOTAL USD 14.75", "blocks": []}]
            if len(calls) == 1:
                pages = pages * 2
            return ModelResponse(parts=[TextPart(json.dumps({"pages": pages}))])

        models = SimpleNamespace(agent=FunctionModel(tool_model), vision=FunctionModel(vision))
        key, path, credit = inputs_for_run([], "minimal")[0]
        with tempfile.TemporaryDirectory() as directory:
            parsed = await ReceiptAgent(models).run(
                {"receipt_id": key, "source": path.name, "path": str(path), "credit": credit},
                Path(directory),
            )
            self.assertIsNone(parsed.error)
            self.assertEqual(len(calls), 2)
            self.assertEqual([page.number for page in parsed.pages], [1])
            self.assertEqual(parsed.total, "14.75")

    async def test_interpretation_failure_retains_extracted_page_text(self):
        def invalid_interpretation(messages, info):
            return ModelResponse(
                parts=[ToolCallPart(info.output_tools[0].name, {"total": "not money"})]
            )

        models = SimpleNamespace(
            agent=FunctionModel(invalid_interpretation),
            vision=TestModel(
                custom_output_text=json.dumps(
                    {
                        "pages": [
                            {"number": 1, "text": "TOTAL USD 14.75", "blocks": ["TOTAL USD 14.75"]},
                        ]
                    }
                )
            ),
        )
        key, path, credit = inputs_for_run([], "minimal")[0]
        source = {"receipt_id": key, "source": path.name, "path": str(path), "credit": credit}
        with tempfile.TemporaryDirectory() as directory:
            result = await ReceiptAgent(models).run(source, Path(directory))
            self.assertTrue(result.error.startswith("Interpretation"))
            self.assertEqual(result.pages[0].text, "TOTAL USD 14.75")
            self.assertEqual(result.receipt_id, key)

    async def test_report_agent_tool_applies_semantic_monetary_flag(self):
        def flag_model(messages, info):
            self.assertEqual(info.output_tools[0].name, "generate_report")
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        info.output_tools[0].name,
                        {
                            "monetary_flags": [
                                {
                                    "receipt_id": "a",
                                    "reason": "Extracted amount is tender, not payable total",
                                }
                            ],
                        },
                    )
                ]
            )

        models = SimpleNamespace(agent=FunctionModel(flag_model))
        with tempfile.TemporaryDirectory() as directory:
            artifacts = await ReportAgent(models).run([receipt("a")], Path(directory))
            report = json.loads(artifacts["report.json"].read_text())
            self.assertEqual(report["entries"][0]["outcome"], "flagged")
            self.assertEqual(report["totals"], {})

    async def test_shared_inference_auth_failure_is_raised_to_caller(self):
        def broken_model(messages, info):
            raise ModelHTTPError(401, "vision", {"secret": "must not be logged"})

        models = SimpleNamespace(
            agent=FunctionModel(tool_model), vision=FunctionModel(broken_model)
        )
        key, path, credit = inputs_for_run([], "minimal")[0]
        sources = [{"receipt_id": key, "source": path.name, "path": str(path), "credit": credit}]
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "Inference configuration failed") as raised:
                await ReceiptAgent(models).run(sources[0], Path(directory))
            self.assertNotIn("must not be logged", str(raised.exception))

    async def test_receipt_outputs_produce_report_despite_corrupt_input(self):
        fields = {
            "merchant": "Fixture Cafe",
            "date_text": None,
            "currency": "USD",
            "transaction_type": "purchase",
            "printed_total": "14.75",
            "total": "14.75",
            "pages": [{"number": 1, "text": "TOTAL $14.75", "blocks": ["TOTAL $14.75"]}],
            "evidence": [{"field": "total", "page": 1, "text": "TOTAL $14.75"}],
        }
        models = SimpleNamespace(
            agent=FunctionModel(tool_model), vision=TestModel(custom_output_text=json.dumps(fields))
        )
        sources = [
            {"receipt_id": key, "source": path.name, "path": str(path), "credit": credit}
            for key, path, credit in inputs_for_run([], "minimal")
            if key != "s04"
        ]
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            records = [await ReceiptAgent(models).run(source, temp / "work") for source in sources]
            artifacts = await ReportAgent(models).run(records, temp / "output")
            result = json.loads(artifacts["report.json"].read_text())
            self.assertEqual(result["status"], "completed_with_flags")
            self.assertEqual([e["outcome"] for e in result["entries"]], ["included", "error"])
            self.assertEqual(result["totals"], {"USD": "14.75"})
            with pdfium.PdfDocument(temp / "output/report.pdf") as pdf:
                self.assertGreaterEqual(len(pdf), 3)
                page = pdf[0]
                text = page.get_textpage()
                summary = text.get_text_range()
                text.close()
                page.close()
                self.assertIn("USD 14.75", summary)
                self.assertIn("v05-control-corrupt.pdf", summary)
                self.assertNotIn("p. ?", summary)


class TransferTests(unittest.TestCase):
    def test_binary_upload_checksum_and_download_path(self):
        from tests.sandbox_http import sandbox_responses

        api = SandboxClient("test", "project")
        data = b"\x00\xffbinary"
        image = "12345678-1234-1234-1234-123456789abc"
        with sandbox_responses(
            {"uuid": "id", "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)},
            data,
        ) as requests:
            self.assertEqual(api.upload(data), {"uuid": "id", "mode": "0600"})
            self.assertEqual(api.download(image, "/app/a b.pdf"), data)
        self.assertEqual(requests[0].content, data)
        self.assertEqual(requests[0].headers["Content-Type"], "application/octet-stream")
        self.assertEqual(requests[1].url.path, f"/sandboxes/v1/inspect/{image}/download")
        self.assertEqual(requests[1].url.params["path"], "/app/a b.pdf")

    def test_retrieval_validates_bytes_before_publishing_success(self):
        data = {"report.json": b"{}", "report.pdf": b"%PDF-test"}
        response = {
            "status": "completed",
            "artifacts": {
                name: {
                    "path": "/app/output/" + name,
                    "bytes": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                }
                for name, raw in data.items()
            },
        }
        api = SandboxClient("test", "project")

        def download(image, path):
            return (
                json.dumps(response).encode()
                if path.endswith("result.json")
                else data[Path(path).name]
            )

        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(api, "download", side_effect=download),
        ):
            retrieve_result(api, "image", directory)
            self.assertEqual((Path(directory) / "report.pdf").read_bytes(), data["report.pdf"])
        response["artifacts"]["report.pdf"]["sha256"] = "bad"
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(api, "download", side_effect=download),
        ):
            with self.assertRaises(RuntimeError):
                retrieve_result(api, "image", directory)
            self.assertFalse((Path(directory) / "result.json").exists())
            self.assertFalse((Path(directory) / "report.json").exists())

    def test_job_retains_output_without_preserving_environment(self):
        from tests.sandbox_http import sandbox_responses

        api = SandboxClient("test", "project")
        with sandbox_responses({"uuid": "job"}) as requests:
            self.assertEqual(
                submit_worker(api, "base", {}, {"NEBIUS_API_KEY": "secret"}, 300), "job"
            )
            body = json.loads(requests[0].content)
            self.assertFalse(body["disposable"])
            self.assertFalse(body["preserve_env"])
            self.assertEqual(body["cwd"], "/app")
            self.assertEqual(body["command"], "/usr/local/bin/python3")
            self.assertEqual(body["env"]["RECEIPT_JOB_TIMEOUT"], "300")
            self.assertNotIn("secret", body["stdin"]["value"])


if __name__ == "__main__":
    unittest.main()

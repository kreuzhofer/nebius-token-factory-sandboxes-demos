"""Two sequential analysis attempts; advance only after independently verified output."""

import hashlib
import json
from pathlib import Path
from uuid import uuid4

from codingagent_demo import run_task

from .failure import run_failed_followup
from .validation import RULES, expected_sales, validate_artifacts

FIXTURES = Path(__file__).parent / "fixtures"
INITIAL_TASK = "Clean the sales data, produce a summary and chart."
FOLLOWUP_TASK = "Break this down by region."


def step_prompt(request, contract, previous):
    prefix = f"/workspace/steps/{contract['step_id']}"
    common = (
        f"{request}\n\nMandatory analysis output contract (additional requested analysis is allowed):\n"
        f"Read /workspace/analysis-step.json. Write new artifacts only under {prefix}. "
        "Use the installed matplotlib with Agg backend; do not install dependencies. "
        "Do not edit inherited files. Return a concise answer describing actual results. "
        "Parse CSV with csv.DictReader and aggregate with decimal.Decimal; convert to float "
        "only for plotting. "
        "summary.json must contain step_id (from analysis-step.json), count (integer), "
        "total (two-decimal string). Create chart.png, a labelled bar chart at least 200x150 "
        "and at most 4096x4096 pixels with PNG text metadata StepId=step_id and Data="
        "json.dumps(the chart's label-to-two-decimal-total mapping). "
        "Use matplotlib savefig(metadata={'StepId': step_id, 'Data': json.dumps(data)}).\n"
    )
    if previous:
        return common + (
            "This is a fresh agent process continuing a saved filesystem. Read "
            "/workspace/analysis-context.json for the preceding request, answer and artifact "
            "references. Read the inherited cleaned.csv and lineage.txt at those references; "
            "do not reread the original CSVs. summary.json must also contain regions "
            "(region -> {count, total}), lineage (EXACT full text of lineage.txt, including "
            "any newline), and cleaned_sha256 (SHA-256 of the inherited cleaned.csv bytes). "
            "Chart the total for each region, including refunds. Do not replace the initial chart."
        )
    return common + (
        f"Input files in /workspace: {json.dumps(contract['inputs'])}. {RULES}\n"
        "Save cleaned.csv with header sale_id,region,amount and normalized retained rows. "
        "Create lineage.txt using a new random UUID generated INSIDE this task. "
        "summary.json must also contain discarded (number of discarded input records). "
        "Chart the overall total as one bar labelled Total."
    )


def run_analysis(
    client,
    config,
    image,
    output,
    *,
    files=None,
    task=INITIAL_TASK,
    followup=FOLLOWUP_TASK,
    timeout=600,
    fail_followup=False,
):
    """Run once per step. Failure retains the prior successful checkpoint and diagnostics."""
    files = [FIXTURES / "sales.csv"] if files is None else list(map(Path, files))
    if not files or any(
        p.is_symlink() or not p.is_file() or p.suffix.lower() != ".csv" for p in files
    ):
        raise ValueError("Supply existing CSV files, not directories or symlinks")
    if len({path.name for path in files}) != len(files):
        raise ValueError("CSV basenames must be unique")
    if not task.strip() or not followup.strip():
        raise ValueError("Both task requests must be nonempty")
    expected = expected_sales(files)
    if files == [FIXTURES / "sales.csv"]:
        if expected != json.loads((FIXTURES / "expected.json").read_text()):
            raise ValueError(
                "Synthetic fixture differs from its independently specified expectations"
            )
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    record = {
        "prepared_runtime": image,
        "steps": [],
        "last_successful_result": None,
        "parent_verification": None,
        "passed": False,
        "error": None,
    }

    def save():
        pending = output / "analysis.json.tmp"
        pending.write_text(json.dumps(record, indent=2))
        pending.replace(output / "analysis.json")

    save()
    previous = None
    try:
        for label, request in (("initial", task), ("followup", followup)):
            step_id = label + "-" + uuid4().hex
            step: dict = {
                "step_id": step_id,
                "request": request,
                "parent_checkpoint": image,
                "execution": None,
                "validation": {"passed": False, "error": "Not run"},
            }
            record["steps"].append(step)
            save()
            step_output = output / label
            step_output.mkdir()
            contract = {
                "step_id": step_id,
                "inputs": [path.name for path in files] if not previous else [],
            }
            contract_file = step_output / "analysis-step.json"
            contract_file.write_text(json.dumps(contract, indent=2))
            uploads = [contract_file]
            if previous:
                context = {
                    "request": previous["request"],
                    "answer": previous["execution"]["answer"],
                    "artifacts": previous["validation"]["artifacts"],
                    "checkpoint": previous["execution"]["image"],
                }
                context_file = step_output / "analysis-context.json"
                context_file.write_text(json.dumps(context, indent=2))
                uploads.append(context_file)
                step["context"] = str(context_file)
            else:
                uploads.extend(files)
            prompt = step_prompt(request, contract, previous)
            (step_output / "prompt.txt").write_text(prompt)
            if previous and fail_followup:
                step["execution"] = run_failed_followup(
                    client,
                    image,
                    step_output / "task",
                    uploads,
                    step_id,
                )
            else:
                step["execution"] = run_task(
                    client,
                    config,
                    image,
                    prompt,
                    files=uploads,
                    output=step_output / "task",
                    timeout=timeout,
                )
            save()
            execution = step["execution"]
            if execution["status"] != "completed" or not execution["image"]:
                break
            step["validation"] = validate_artifacts(
                execution["archive"],
                step_id,
                expected,
                step_output / "artifacts",
                previous=previous["validation"] if previous else None,
            )
            if not step["validation"]["passed"]:
                break
            image = execution["image"]
            record["last_successful_result"] = {
                "step_id": step_id,
                "checkpoint": image,
                "answer": execution["answer"],
                "artifacts": step["validation"]["artifacts"],
            }
            previous = step
            save()
        record["passed"] = len(record["steps"]) == 2 and all(
            step["validation"]["passed"] for step in record["steps"]
        )
    except (Exception, KeyboardInterrupt) as exc:
        record["error"] = (
            f"Analysis attempt interrupted or failed: {type(exc).__name__}; no automatic retry"
        )
        if record["steps"] and record["steps"][-1]["execution"] is None:
            record["steps"][-1]["execution"] = {
                "status": "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
                "operation_id": None,
                "image": None,
                "error": record["error"],
            }
    finally:
        # Read the original checkpoint independently after the child attempt, even on failure.
        first = record["steps"][0] if record["steps"] else None
        if first and first["validation"]["passed"] and len(record["steps"]) == 2:
            check = {"checkpoint": first["execution"]["image"], "passed": False, "error": None}
            record["parent_verification"] = check
            save()
            try:
                original = Path(first["execution"]["archive"]).read_bytes()
                retrieved = client.download(
                    check["checkpoint"],
                    first["execution"]["result_dir"] + "/workspace.tar.gz",
                )
                check["archive_sha256"] = hashlib.sha256(retrieved).hexdigest()
                check["passed"] = retrieved == original
                if not check["passed"]:
                    check["error"] = "Parent checkpoint archive changed"
                artifacts = first["validation"]["artifacts"]
                hashes = {ref["workspace_path"]: ref["sha256"] for ref in artifacts.values()}
                child_path = "/workspace/steps/" + record["steps"][1]["step_id"]
                script = (
                    "import hashlib, json\nfrom pathlib import Path\n"
                    f"expected = {hashes!r}\n"
                    "for name, digest in expected.items():\n"
                    "    assert hashlib.sha256(Path(name).read_bytes()).hexdigest() == digest, name\n"
                    f"assert not Path({child_path!r}).exists(), 'Child files appeared in parent'\n"
                    "print('Parent artifacts unchanged; child output directory absent')\n"
                )
                check["operation_id"] = client.submit(
                    check["checkpoint"],
                    command="/usr/local/bin/python3",
                    args=["-u", "-"],
                    stdin=script,
                    timeout=30,
                    networking=False,
                    disposable=True,
                )
                save()
                execution = client.wait(check["operation_id"], 150).execution_result(check=False)
                check.update(stdout=execution.stdout, stderr=execution.stderr)
                check["passed"] = check["passed"] and execution.successful
                if not execution.successful:
                    check["error"] = "Independent parent checkpoint check failed"
            except (Exception, KeyboardInterrupt) as exc:
                check["passed"] = False
                check["error"] = f"Parent checkpoint could not be verified: {type(exc).__name__}"
            record["passed"] = record["passed"] and check["passed"]
        save()
    return record

"""Select only validated upgrade candidates; preserve the baseline on rejection."""

import json
from pathlib import Path

from codingagent_demo import run_task
from codingagent_demo.monitor import save_json

from .sandbox import CHECKS, execute

FIXTURE = Path(__file__).with_name("fixture")
TASK = (
    "Upgrade this SQLite ledger from SQLAlchemy==1.4.54 to SQLAlchemy==2.0.36. "
    "Update /workspace/requirements.txt, install it with /usr/local/bin/python3 -m pip, "
    "and migrate ledger.py for SQLAlchemy 2. Preserve the existing ledger.db entries "
    "and the CLI behavior: init DATABASE; add DATABASE CATEGORY INTEGER_CENTS; report DATABASE. "
    "Report prints JSON with categories (category to integer cents), total, count. "
    "Refunds use negative cents. Writes must persist after the process exits. "
    "Do not edit check_ledger.py or bypass tests. Run /usr/local/bin/python3 "
    "/workspace/check_ledger.py --version 2.0.36 and report actual results. "
    "Use explicit transactions and connections; Engine.execute and implicit autocommit "
    "were removed. Do not alter inherited ledger data or install a different version."
)


def validate(client, checkpoint, output, version, *, manifest=None):
    args = ["--version", version]
    files = {}
    if manifest is not None:
        files["/opt/upgrade/expected-baseline.json"] = json.dumps(manifest).encode()
        args += ["--baseline-manifest", "/opt/upgrade/expected-baseline.json"]
    result = execute(client, checkpoint, "check_ledger.py", output, args=args, files=files)
    result["passed"] = False
    if result["status"] != "incomplete":
        try:
            result["report"] = json.loads(result["stdout"])
            report = result["report"]
            if report["status"] not in {"passed", "failed", "incomplete"}:
                raise ValueError("Invalid trusted check status")
            result["passed"] = (
                result["status"] == "passed"
                and report["status"] == "passed"
                and report["passed"] is True
            )
            result["status"] = (
                "incomplete"
                if report["status"] == "incomplete"
                else "passed"
                if result["passed"]
                else "failed"
            )
            if not result["passed"]:
                result["error"] = report.get("error") or "Trusted checks did not pass"
        except (ValueError, KeyError, TypeError):
            result.update(status="incomplete", error="Trusted check report missing or invalid")
    save_json(Path(output) / "validation.json", result)
    return result


def prepare_baseline(client, image, output):
    files = {"/workspace/" + p.name: p.read_bytes() for p in FIXTURE.iterdir() if p.is_file()}
    files["/workspace/check_ledger.py"] = (CHECKS / "check_ledger.py").read_bytes()
    result = execute(
        client,
        image,
        "prepare.py",
        output,
        files=files,
        disposable=False,
        networking=True,
        timeout=300,
    )
    result.update(archive=None, manifest=None, validation=None)
    if result["status"] == "passed":
        result["manifest"] = json.loads(
            client.download(result["image"], "/opt/upgrade/baseline.json")
        )
        archive = Path(output) / "workspace.tar.gz"
        archive.write_bytes(client.download(result["image"], "/opt/upgrade/workspace.tar.gz"))
        result["archive"] = str(archive)
        result["validation"] = validate(
            client,
            result["image"],
            Path(output) / "validation",
            "1.4.54",
            manifest=result["manifest"],
        )
    return result


def run_demo(client, config, image, output, *, scenarios=("passing", "rollback"), timeout=600):
    """Run each requested scenario once from one verified baseline; save upgrade.json."""
    if not scenarios or any(name not in {"passing", "rollback"} for name in scenarios):
        raise ValueError("Choose passing and/or rollback scenarios")
    if len(set(scenarios)) != len(scenarios) or not 60 <= timeout <= 600:
        raise ValueError("Scenarios must be unique and timeout must be between 60 and 600")
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    record = {
        "runtime_checkpoint": image,
        "baseline": None,
        "scenarios": [],
        "demonstration_complete": False,
        "exit_code": 1,
        "error": None,
    }

    def save():
        save_json(output / "upgrade.json", record)

    save()
    try:
        baseline = prepare_baseline(client, image, output / "baseline")
        record["baseline"] = baseline
        save()
        if not baseline["validation"] or not baseline["validation"]["passed"]:
            record["error"] = "Baseline could not be verified; no upgrade attempted"
            return record
        for name in scenarios:
            folder = output / name
            folder.mkdir()
            scenario = {
                "name": name,
                "parent_checkpoint": baseline["image"],
                "agent": None,
                "validation": None,
                "selected_result": None,
                "injection": None,
                "injected_validation": None,
                "baseline_verification": None,
                "error": None,
                "outcome": "upgrade_rejected",
                "demonstration_complete": False,
            }
            record["scenarios"].append(scenario)
            save()
            (folder / "prompt.txt").write_text(TASK)
            try:
                scenario["agent"] = run_task(
                    client,
                    config,
                    baseline["image"],
                    TASK,
                    output=folder / "agent",
                    timeout=timeout,
                )
                save()
                agent = scenario["agent"]
                if agent["status"] == "completed" and agent["image"]:
                    scenario["validation"] = validate(
                        client, agent["image"], folder / "validation", "2.0.36"
                    )
                    save()
                    if scenario["validation"]["passed"]:
                        if name == "passing":
                            scenario.update(
                                outcome="upgrade_accepted",
                                demonstration_complete=True,
                                selected_result={
                                    "checkpoint": agent["image"],
                                    "archive": agent["archive"],
                                    "verified": True,
                                },
                            )
                        else:
                            scenario["injection"] = execute(
                                client,
                                agent["image"],
                                "inject.py",
                                folder / "injection",
                                disposable=False,
                            )
                            save()
                            injection = scenario["injection"]
                            if injection["status"] == "passed":
                                scenario["injected_validation"] = validate(
                                    client,
                                    injection["image"],
                                    folder / "injected-validation",
                                    "2.0.36",
                                )
                                save()
                                archive = folder / "injection" / "workspace.tar.gz"
                                archive.write_bytes(
                                    client.download(
                                        injection["image"], "/opt/upgrade/injected-workspace.tar.gz"
                                    )
                                )
                                injection["archive"] = str(archive)
            except (Exception, KeyboardInterrupt) as exc:
                scenario["error"] = (
                    f"Upgrade interrupted or failed: {type(exc).__name__}; no automatic retry"
                )
            finally:
                if scenario["outcome"] != "upgrade_accepted":
                    check = validate(
                        client,
                        baseline["image"],
                        folder / "baseline-verification",
                        "1.4.54",
                        manifest=baseline["manifest"],
                    )
                    scenario["baseline_verification"] = check
                    scenario["selected_result"] = {
                        "checkpoint": baseline["image"],
                        "archive": baseline["archive"],
                        "verified": check["passed"],
                    }
                    scenario["outcome"] = {
                        "passed": "upgrade_rejected_baseline_verified",
                        "failed": "upgrade_rejected_baseline_failed",
                        "incomplete": "upgrade_rejected_baseline_unverified",
                    }[check["status"]]
                    injected = scenario["injected_validation"]
                    scenario["demonstration_complete"] = bool(
                        name == "rollback"
                        and injected
                        and injected["status"] == "failed"
                        and check["passed"]
                        and not scenario["error"]
                    )
                save()
            if scenario["agent"] and scenario["agent"]["status"] in {"interrupted", "cancelled"}:
                break
        record["demonstration_complete"] = all(
            s["demonstration_complete"] for s in record["scenarios"]
        )
        record["exit_code"] = (
            0 if all(s["outcome"] == "upgrade_accepted" for s in record["scenarios"]) else 1
        )
    except (Exception, KeyboardInterrupt) as exc:
        record["error"] = f"Demo interrupted or failed: {type(exc).__name__}; no automatic retry"
    finally:
        save()
    return record

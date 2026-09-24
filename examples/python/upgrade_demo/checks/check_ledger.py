"""Trusted, offline behavior checks executed in a disposable checkpoint child."""

import argparse
import hashlib
import importlib.metadata
import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

SEED_ROWS = [("food", 1200), ("food", -200), ("travel", 500)]
SEED_REPORT = {"categories": {"food": 1000, "travel": 500}, "total": 1500, "count": 3}


def ledger(workspace, *args):
    result = subprocess.run(
        [sys.executable, str(workspace / "ledger.py"), *map(str, args)],
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode:
        raise AssertionError(f"Ledger command failed: {result.stderr[-2000:]}")
    return json.loads(result.stdout) if result.stdout.strip() else None


def rows(database):
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        return connection.execute("SELECT category, cents FROM expenses ORDER BY id").fetchall()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=Path("/workspace"))
    parser.add_argument("--version", required=True)
    parser.add_argument("--baseline-manifest", type=Path)
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    report: dict[str, object] = {
        "passed": False,
        "status": "failed",
        "version": None,
        "error": None,
    }
    try:
        report["version"] = importlib.metadata.version("SQLAlchemy")
        assert report["version"] == args.version, "Incorrect installed SQLAlchemy version"
        assert (workspace / "requirements.txt").read_text().strip() == (
            "SQLAlchemy==" + args.version
        ), "Incorrect dependency pin"
        if args.baseline_manifest:
            expected = json.loads(args.baseline_manifest.read_text())
            actual = {
                p.relative_to(workspace).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in workspace.rglob("*")
                if p.is_file()
            }
            assert actual == expected, "Baseline files changed or candidate files appeared"
        assert rows(workspace / "ledger.db") == SEED_ROWS, "Pre-existing ledger entries changed"
        assert ledger(workspace, "report", "ledger.db") == SEED_REPORT, "Incorrect seeded totals"
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "new.db"
            ledger(workspace, "init", database)
            assert ledger(workspace, "report", database) == {
                "categories": {},
                "total": 0,
                "count": 0,
            }, "New ledger must be empty"
            entries = [("food", 333), ("food", -33), ("travel", 200)]
            for category, cents in entries:
                ledger(workspace, "add", database, category, cents)
            assert rows(database) == entries, "New transactions were not persisted"
            assert ledger(workspace, "report", database) == {
                "categories": {"food": 300, "travel": 200},
                "total": 500,
                "count": 3,
            }, "Incorrect new transaction totals"
        assert rows(workspace / "ledger.db") == SEED_ROWS, "Validation changed inherited entries"
        report["passed"] = True
        report["status"] = "passed"
    except subprocess.TimeoutExpired as exc:
        report.update(status="incomplete", error=f"TimeoutExpired: {exc}")
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    print(json.dumps(report))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

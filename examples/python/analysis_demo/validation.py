"""Check declared analysis artifacts without extracting or executing agent code."""

import csv
import hashlib
import io
import json
import re
import tarfile
from decimal import Decimal
from pathlib import Path

from PIL import Image

COLUMNS = ["sale_id", "region", "amount"]
RULES = (
    "Read input CSVs in filename order and rows in file order. Require sale_id,region,amount "
    "columns. Strip whitespace in each field; title-case region. Keep amounts only if they "
    "match -?[0-9]+\\.[0-9]{2}. Retain refunds and zero. Discard rows with missing IDs/regions "
    "or invalid amounts. Among valid rows keep the first occurrence of each sale_id across "
    "all files. Use exact decimal arithmetic. Preserve retained row order."
)


def expected_sales(files):
    """Independent oracle for the documented sales CSV contract, including custom input."""
    rows = []
    seen = set()
    discarded = 0
    for path in sorted(map(Path, files), key=lambda path: path.name):
        with path.open(newline="", encoding="utf-8") as source:
            reader = csv.DictReader(source)
            if reader.fieldnames != COLUMNS:
                raise ValueError("CSV header must be sale_id,region,amount")
            for row in reader:
                if None in row or any(value is None for value in row.values()):
                    raise ValueError("CSV rows must have exactly three columns")
                sale_id, region, amount = (row[key].strip() for key in COLUMNS)
                if (
                    not sale_id
                    or not region
                    or sale_id in seen
                    or not re.fullmatch(r"-?[0-9]+\.[0-9]{2}", amount)
                ):
                    discarded += 1
                    continue
                seen.add(sale_id)
                rows.append({"sale_id": sale_id, "region": region.title(), "amount": amount})
    if not rows:
        raise ValueError("At least one valid sale is required")
    regions = {}
    for row in rows:
        group = regions.setdefault(row["region"], {"count": 0, "total": Decimal(0)})
        group["count"] += 1
        group["total"] += Decimal(row["amount"])
    return {
        "count": len(rows),
        "total": format(sum((Decimal(row["amount"]) for row in rows), Decimal(0)), ".2f"),
        "discarded": discarded,
        "regions": {
            key: {"count": value["count"], "total": format(value["total"], ".2f")}
            for key, value in sorted(regions.items())
        },
        "rows": rows,
    }


def read_artifact(archive, name):
    matches = [member for member in archive.getmembers() if member.name == "workspace/" + name]
    if len(matches) != 1 or not matches[0].isfile() or matches[0].size > 10 * 1024**2:
        raise ValueError(f"Missing, ambiguous, or oversized artifact: {name}")
    with archive.extractfile(matches[0]) as source:
        return source.read()


def validate_artifacts(archive_path, step_id, expected, output, *, previous=None):
    """Separate correctness from execution; return diagnostics even for malformed output."""
    result = {"passed": False, "error": None, "artifacts": {}}
    try:
        prefix = f"steps/{step_id}/"
        names = ["summary.json", "chart.png"]
        if previous is None:
            names += ["cleaned.csv", "lineage.txt"]
        with tarfile.open(archive_path, "r:gz") as archive:
            files = {name: read_artifact(archive, prefix + name) for name in names}
        output = Path(output)
        output.mkdir(parents=True, exist_ok=True)
        for name, content in files.items():
            (output / name).write_bytes(content)
            result["artifacts"][name] = {
                "workspace_path": "/workspace/" + prefix + name,
                "local_path": str(output / name),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        summary = json.loads(files["summary.json"])
        if not isinstance(summary, dict) or summary.get("step_id") != step_id:
            raise ValueError("Summary belongs to a different step")
        for key in (
            ("count", "total", "discarded") if previous is None else ("count", "total", "regions")
        ):
            if summary.get(key) != expected[key]:
                raise ValueError(f"Incorrect summary {key}")
        if previous is None:
            reader = csv.DictReader(io.StringIO(files["cleaned.csv"].decode()))
            if reader.fieldnames != COLUMNS or list(reader) != expected["rows"]:
                raise ValueError("Cleaned rows differ from independently computed input")
            lineage = files["lineage.txt"].decode()
            if not lineage.strip():
                raise ValueError("Initial task must create a nonempty lineage file")
            result.update(lineage=lineage)
        else:
            if summary.get("lineage") != previous["lineage"]:
                raise ValueError("Follow-up did not read the initial task's lineage file")
            if summary.get("cleaned_sha256") != previous["artifacts"]["cleaned.csv"]["sha256"]:
                raise ValueError("Follow-up did not use the initial cleaned data")
        chart_data = (
            {key: value["total"] for key, value in expected["regions"].items()}
            if previous
            else {"Total": expected["total"]}
        )
        with Image.open(io.BytesIO(files["chart.png"])) as image:
            if image.format != "PNG" or not (
                200 <= image.width <= 4096 and 150 <= image.height <= 4096
            ):
                raise ValueError("Chart must be a bounded, readable PNG")
            image.load()
            if (
                image.info.get("StepId") != step_id
                or json.loads(image.info.get("Data", "null")) != chart_data
            ):
                raise ValueError("Chart metadata does not describe the requested step and values")
            if image.convert("RGB").getcolors(maxcolors=1) is not None:
                raise ValueError("Chart is blank")
        result.update(passed=True, summary=summary)
    except Exception as exc:
        result["error"] = f"Artifact validation failed: {type(exc).__name__}: {exc}"
    return result

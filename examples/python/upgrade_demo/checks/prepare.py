"""Install and seed the baseline before any coding task runs."""

import hashlib
import json
import subprocess
import sys
import tarfile
from pathlib import Path

workspace = Path("/workspace")
subprocess.run(
    [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "-r",
        str(workspace / "requirements.txt"),
    ],
    check=True,
)
for args in (
    ("init", "ledger.db"),
    ("add", "ledger.db", "food", "1200"),
    ("add", "ledger.db", "food", "-200"),
    ("add", "ledger.db", "travel", "500"),
):
    subprocess.run([sys.executable, str(workspace / "ledger.py"), *args], cwd=workspace, check=True)
manifest = {
    p.relative_to(workspace).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
    for p in workspace.rglob("*")
    if p.is_file()
}
Path("/opt/upgrade/baseline.json").write_text(json.dumps(manifest, indent=2))
with tarfile.open("/opt/upgrade/workspace.tar.gz", "w:gz") as archive:
    archive.add(workspace, arcname="workspace")
print("Baseline dependency installed and ledger seeded")

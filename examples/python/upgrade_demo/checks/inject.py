"""Deliberately disable the ledger after a real upgrade has independently passed."""

import tarfile
from pathlib import Path

workspace = Path("/workspace")
ledger = workspace / "ledger.py"
(workspace / "ledger.before-injection.py").write_bytes(ledger.read_bytes())
message = "Injected regression: ledger entrypoint deliberately disabled"
(workspace / "injected-regression.txt").write_text(message + "\n")
ledger.write_text(f"raise RuntimeError({message!r})\n")
with tarfile.open("/opt/upgrade/injected-workspace.tar.gz", "w:gz") as archive:
    archive.add(workspace, arcname="workspace")
print(message)

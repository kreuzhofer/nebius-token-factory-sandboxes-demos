"""Prepare analysis libraries once on top of the shared coding runtime."""

import json
from pathlib import Path

from codingagent_demo import build_runtime


def build_analysis_runtime(client, output):
    output = Path(output)
    image = build_runtime(client, output / "coding")
    pins = Path(__file__).with_name("requirements-runtime.txt").read_text()
    script = (
        "import importlib.metadata, json, platform, subprocess\n"
        f"pins = {pins.splitlines()!r}\n"
        "subprocess.run(['/usr/local/bin/python3', '-m', 'pip', 'install', "
        "'--disable-pip-version-check', *pins], check=True)\n"
        "import matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\n"
        "plt.bar(['Total'], [1]); plt.close()\n"
        "print(json.dumps({'python': platform.python_version(), 'packages': "
        "{p.split('==')[0]: importlib.metadata.version(p.split('==')[0]) for p in pins}}))\n"
    )
    record = {
        "parent_image": image,
        "image": None,
        "operation_id": None,
        "requirements": pins.splitlines(),
        "status": "preparing",
        "error": None,
    }
    manifest = output / "runtime.json"
    try:
        record["operation_id"] = client.submit(
            image,
            command="/usr/local/bin/python3",
            args=["-u", "-"],
            stdin=script,
            timeout=300,
            networking=True,
            disposable=False,
            max_layer_bytes=2 * 1024**3,
            output_limit=1024**2,
        )
        record["status"] = "running"
        manifest.write_text(json.dumps(record, indent=2))
        print(f"Analysis runtime build: {record['operation_id']}", flush=True)
        operation = client.wait(record["operation_id"], 420)
        execution = operation.execution_result(check=False)
        (output / "build.log").write_text(execution.stdout + execution.stderr)
        if not execution.successful:
            raise RuntimeError("Analysis dependency build failed; see build.log")
        record.update(image=operation.require_image(), status="completed")
        record["versions"] = json.loads(execution.stdout.strip().splitlines()[-1])
    except (Exception, KeyboardInterrupt) as exc:
        record.update(status="failed", error=f"Build unconfirmed or failed: {type(exc).__name__}")
        raise
    finally:
        manifest.write_text(json.dumps(record, indent=2))
    return record["image"]

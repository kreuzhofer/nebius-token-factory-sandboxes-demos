"""Run the demo's preparation and trusted checks through the shared sandbox client."""

from pathlib import Path

from codingagent_demo.monitor import save_json

CHECKS = Path(__file__).with_name("checks")


def execute(
    client,
    parent,
    script,
    output,
    *,
    args=(),
    files=None,
    disposable=True,
    networking=False,
    timeout=60,
):
    """Persist identity before waiting and retain diagnostics even when a check is unconfirmed."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    record = {
        "parent_checkpoint": parent,
        "operation_id": None,
        "image": None,
        "status": "incomplete",
        "stdout": "",
        "stderr": "",
        "error": None,
    }
    try:
        uploads = {name: client.upload(content) for name, content in (files or {}).items()}
        remote_script = "/opt/upgrade/" + script
        uploads[remote_script] = client.upload((CHECKS / script).read_bytes())
        record["operation_id"] = client.submit(
            parent,
            command="/usr/local/bin/python3",
            args=["-u", remote_script, *args],
            cwd="/workspace",
            files=uploads,
            timeout=timeout,
            networking=networking,
            disposable=disposable,
            max_layer_bytes=2 * 1024**3,
            output_limit=1024**2,
        )
        save_json(output / "run.json", record)
        print(f"{script}: {record['operation_id']}", flush=True)
        operation = client.wait(record["operation_id"], timeout + 120, check=False)
        execution = operation.execution_result(check=False)
        record.update(stdout=execution.stdout, stderr=execution.stderr, image=operation.image)
        if execution.timed_out or execution.signal not in (None, -1, 0):
            record["error"] = "Execution interrupted or timed out"
        elif execution.successful:
            if not disposable:
                record["image"] = operation.require_image()
            record["status"] = "passed"
        else:
            record.update(status="failed", error="Sandbox command failed")
    except (Exception, KeyboardInterrupt) as exc:
        record["error"] = f"Result unconfirmed: {type(exc).__name__}; no automatic retry"
    finally:
        (output / "stdout.log").write_text(record["stdout"])
        (output / "stderr.log").write_text(record["stderr"])
        save_json(output / "run.json", record)
    return record

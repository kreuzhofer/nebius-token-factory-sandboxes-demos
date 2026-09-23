"""Workspace-job outcomes and artifact recovery shared by coding tasks and the deadline probe.

Provider transport and operation lifecycle remain in nebius_sandbox.SandboxClient.
This module owns only the coding demo's result envelope and artifact conventions.
"""

import json
import time
from pathlib import Path

from http_transport import TransportError


def run_workspace_job(
    client,
    image,
    *,
    output,
    timeout,
    files,
    script,
    env=None,
    networking=False,
    metadata=None,
    result_dir="/opt/coding-result",
):
    """Submit once, save identity early, and retrieve an explicit outcome and available files."""
    maximum = client.limits().get("instance_max_timeout")
    if not isinstance(maximum, int) or not 1 <= timeout <= maximum:
        raise ValueError("Job timeout must be positive and within the reported account limit")
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    record = {
        "operation_id": None,
        "image": None,
        "status": "preparing",
        "runtime_image": image,
        "result_dir": result_dir,
        **(metadata or {}),
        "timeout": timeout,
        "answer": "",
        "archive": None,
        "logs": [],
        "error": None,
    }
    start = time.monotonic()

    def save():
        record["elapsed_seconds"] = round(time.monotonic() - start, 3)
        (output / "job.json").write_text(json.dumps(record, indent=2))

    try:
        operation_id = client.submit(
            image,
            command="/usr/local/bin/python3",
            args=["-u", script],
            cwd="/workspace",
            files=files,
            env=env,
            timeout=timeout,
            networking=networking,
            disposable=False,
            max_layer_bytes=2 * 1024**3,
            output_limit=1024**2,
        )
        record.update(operation_id=operation_id, status="running")
        save()
        print(f"Task job: {operation_id}", flush=True)
        operation = client.wait(
            operation_id,
            timeout + 120,
            check=False,
            on_status=lambda status: print(f"Sandbox: {status}", flush=True),
        )
        record["image"] = operation.image
        if operation.status == "CANCELLED":
            record.update(status="cancelled", error="Sandbox operation was cancelled")
        elif operation.status != "SUCCESS":
            record.update(status="failed", error="Sandbox operation failed")
        else:
            execution = operation.execution_result(check=False)
            for name, content in (
                ("process.stdout.log", execution.stdout),
                ("process.stderr.log", execution.stderr),
            ):
                if content:
                    path = output / name
                    path.write_text(content)
                    record["logs"].append(str(path))
            if execution.timed_out:
                record.update(status="timed_out", error="Sandbox execution deadline reached")
            elif not execution.successful:
                record.update(status="failed", error="Sandbox process failed")
            else:
                try:
                    payload = json.loads(
                        client.download(operation.require_image(), result_dir + "/result.json")
                    )
                    if payload["status"] not in {"completed", "failed", "timed_out"}:
                        raise ValueError("Invalid worker status")
                    record.update(
                        status=payload["status"],
                        answer=payload.get("answer", ""),
                        error=payload.get("error"),
                    )
                except (TransportError, ValueError, KeyError, TypeError) as exc:
                    record.update(
                        status="failed",
                        error=f"Task result summary unavailable or invalid: {type(exc).__name__}",
                    )
        if operation.image:
            for name in ("workspace.tar.gz", "events.jsonl", "stderr.log"):
                try:
                    data = client.download(operation.image, result_dir + "/" + name)
                except TransportError:
                    continue
                path = output / name
                path.write_bytes(data)
                if name == "workspace.tar.gz":
                    record["archive"] = str(path)
                else:
                    record["logs"].append(str(path))
        if record["status"] == "completed" and record["archive"] is None:
            record.update(
                status="failed", error="Agent completed but its workspace archive is unavailable"
            )
    except (TransportError, TimeoutError, KeyboardInterrupt) as exc:
        record.update(
            status="interrupted",
            error=f"Result unconfirmed: {type(exc).__name__}; do not resubmit automatically",
        )
    except Exception as exc:
        record.update(
            status="failed",
            error=f"Task preparation or result decoding failed: {type(exc).__name__}",
        )
    finally:
        save()
        (output / "result.json").write_text(json.dumps(record, indent=2))
    return record

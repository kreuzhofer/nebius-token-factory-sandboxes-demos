"""Workspace-job outcomes and artifact recovery shared by coding tasks and the deadline probe.

Provider transport and operation lifecycle remain in nebius_sandbox.SandboxClient.
This module owns only the coding demo's result envelope and artifact conventions.
"""

import json
import time
from pathlib import Path

from http_transport import TransportError
from sandbox_events import EventStreamError

from .monitor import Transcript, save_json, task_lock


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
    stream=False,
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
        "stream": stream,
        "started_at": time.time(),
        "deadline_at": time.time() + timeout + 120,
        **(metadata or {}),
        "timeout": timeout,
        "answer": "",
        "archive": None,
        "logs": [],
        "error": None,
    }
    with task_lock(output):
        try:
            _start_job(
                client,
                image,
                record,
                output,
                timeout=timeout,
                files=files,
                script=script,
                env=env,
                networking=networking,
            )
        except (TransportError, TimeoutError, KeyboardInterrupt) as exc:
            _interrupted(record, exc)
        except Exception as exc:
            record.update(status="failed", error=f"Task preparation failed: {type(exc).__name__}")
        else:
            return _observe_job(client, record, output)
        _save_result(output, record)
        return record


def _start_job(client, image, record, output, *, timeout, files, script, env, networking):
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
    save_json(output / "job.json", record)
    print(f"Task job: {operation_id}", flush=True)


def monitor_task(client, output):
    """Recover an existing task using durable state; never submit or upload anything."""
    output = Path(output).resolve()
    with task_lock(output):
        record = json.loads((output / "job.json").read_text())
        if not record.get("operation_id") or "deadline_at" not in record:
            raise ValueError("Task has no resumable operation identity or original deadline")
        record["stream"] = True
        return _observe_job(client, record, output)


def _interrupted(record, exc):
    detail = str(exc) if isinstance(exc, EventStreamError) else type(exc).__name__
    record.update(
        status="interrupted",
        error=f"Result unconfirmed: {detail}; do not resubmit automatically",
    )


def _save_result(output, record):
    record["elapsed_seconds"] = round(time.time() - record["started_at"], 3)
    save_json(output / "job.json", record)
    save_json(output / "result.json", record)


def _observe_job(client, record, output):
    try:
        remaining = record["deadline_at"] - time.time()
        if record.get("stream"):
            transcript = Transcript(output, record)
            operation = (
                client.get_operation(record["operation_id"]) if transcript.complete else None
            )
            if operation is None or not operation.done:
                operation = client.wait_with_events(
                    record["operation_id"],
                    remaining,
                    after=transcript.cursor,
                    on_event=transcript.capture,
                    on_warning=transcript.warn,
                )
        else:
            operation = client.wait(
                record["operation_id"],
                max(0, remaining),
                check=False,
                on_status=lambda status: print(f"Sandbox: {status}", flush=True),
            )
        record.update(error=None, logs=[], archive=None)
        _collect_result(client, operation, record, output)
    except (TransportError, TimeoutError, KeyboardInterrupt) as exc:
        _interrupted(record, exc)
    except Exception as exc:
        record.update(status="failed", error=f"Task result decoding failed: {type(exc).__name__}")
    finally:
        _save_result(output, record)
    return record


def _collect_result(client, operation, record, output):
    record["image"] = operation.image
    if operation.status == "CANCELLED":
        record.update(status="cancelled", error="Sandbox operation was cancelled")
    elif operation.status != "SUCCESS":
        record.update(status="failed", error="Sandbox operation failed")
    else:
        execution = operation.execution_result(
            check=False, allow_truncated=record.get("stream", False)
        )
        if execution.output_truncated:
            message = "Live transcript is incomplete: sandbox output limit reached"
            warnings = record.setdefault("monitoring", {"warnings": []})["warnings"]
            record["monitoring"]["complete"] = False
            if message not in warnings:
                warnings.append(message)
                print("Monitor warning: " + message, flush=True)
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
                    client.download(
                        operation.require_image(), record["result_dir"] + "/result.json"
                    )
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
                data = client.download(operation.image, record["result_dir"] + "/" + name)
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

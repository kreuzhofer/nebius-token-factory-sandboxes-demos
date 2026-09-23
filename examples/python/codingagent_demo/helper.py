"""One task in one fresh sandbox, with locally persisted identity and results."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from .job import run_workspace_job
from .runtime import MODEL, OPENCODE_VERSION


@dataclass(frozen=True)
class AgentConfig:
    api_key: str = field(repr=False)
    model: str = MODEL
    base_url: str = "https://api.tokenfactory.nebius.com/v1"


def run_task(client, config, image, task, *, files=(), output, timeout=300, stream=False):
    """Block for one unattended task; return outcome and paths to retrieved artifacts.

    The image must be built with build_runtime() or descend from that runtime.
    A lost connection returns an interrupted outcome with the known operation ID
    and never resubmits work. Each attempt owns a unique worker result directory.
    """
    if not task.strip() or not config.api_key or not config.model:
        raise ValueError("Task, inference key and model are required")
    maximum = client.limits().get("instance_max_timeout")
    if not isinstance(maximum, int) or not 60 <= timeout <= maximum:
        raise ValueError("Task timeout must be between 60 seconds and the reported account limit")
    uploads = {}
    for source in map(Path, files):
        if source.is_symlink() or not source.exists():
            raise ValueError("Inputs must be existing regular files or directories, not symlinks")
        paths = sorted(source.rglob("*")) if source.is_dir() else [source]
        for path in paths:
            relative = path.relative_to(source.parent)
            if any(
                p in {".git", ".venv", "__pycache__", "node_modules", ".env"}
                for p in relative.parts
            ):
                continue
            if path.is_symlink():
                raise ValueError("Input trees must not contain symlinks")
            if not path.is_file():
                continue
            destination = "/workspace/" + relative.as_posix()
            if destination in uploads:
                raise ValueError("Input paths overlap inside the workspace")
            uploads[destination] = client.upload(path.read_bytes())
    result_dir = "/opt/coding-results/" + uuid4().hex
    request = {
        "result_dir": result_dir,
        "stream": stream,
        "task": task,
        "model": config.model,
        "base_url": config.base_url,
        "timeout": timeout,
        "opencode_version": OPENCODE_VERSION,
    }
    uploads["/opt/coding-request.json"] = client.upload(json.dumps(request).encode())
    uploads["/opt/coding-worker.py"] = client.upload(
        Path(__file__).with_name("worker.py").read_bytes()
    )
    return run_workspace_job(
        client,
        image,
        output=output,
        timeout=timeout,
        files=uploads,
        script="/opt/coding-worker.py",
        env={"NEBIUS_API_KEY": config.api_key},
        networking=True,
        result_dir=result_dir,
        stream=stream,
        metadata={"model": config.model, "opencode_version": OPENCODE_VERSION},
    )

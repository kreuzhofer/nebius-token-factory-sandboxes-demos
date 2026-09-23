"""Execute the pinned agent inside a task sandbox and publish its result files."""

import json
import os
import selectors
import signal
import subprocess
import sys
import tarfile
import time
from contextlib import ExitStack
from pathlib import Path

WORKSPACE = Path("/workspace")


def run_agent(command, *, cwd, env, output, key, timeout, live=False):
    """Capture a child process and optionally publish redacted output before it exits."""
    secret = key.encode()
    if not secret:
        raise ValueError("An inference key is required")
    deadline = time.monotonic() + timeout
    with ExitStack() as stack:
        destinations = {
            "stdout": stack.enter_context((Path(output) / "events.jsonl").open("wb")),
            "stderr": stack.enter_context((Path(output) / "stderr.log").open("wb")),
        }
        selector = stack.enter_context(selectors.DefaultSelector())
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        pending = {"stdout": b"", "stderr": b""}
        for name in pending:
            pipe = stack.enter_context(getattr(process, name))
            selector.register(pipe, selectors.EVENT_READ, name)

        def publish(name, data, final=False):
            safe = (pending[name] + data).replace(secret, b"[REDACTED]")
            held = 0
            if not final:
                # Hold only a suffix that could become a key on the next pipe read.
                for size in range(min(len(secret) - 1, len(safe)), 0, -1):
                    if safe.endswith(secret[:size]):
                        held = size
                        break
            pending[name] = safe[-held:] if held else b""
            ready = safe[:-held] if held else safe
            destinations[name].write(ready)
            destinations[name].flush()
            if live and ready:
                terminal = getattr(sys, name).buffer
                terminal.write(ready)
                terminal.flush()

        try:
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, timeout)
                for ready, _ in selector.select(min(remaining, 0.2)):
                    data = os.read(ready.fd, 65536)
                    publish(ready.data, data, final=not data)
                    if not data:
                        selector.unregister(ready.fileobj)
            return process.wait(timeout=max(0.001, deadline - time.monotonic()))
        finally:
            if process.poll() is None or selector.get_map():
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
            # An unfinished key prefix on timeout must not leak into diagnostics.
            for name, tail in pending.items():
                if tail:
                    pending[name] = b""
                    publish(name, b"[REDACTED]", final=True)


def main():
    request = json.loads(Path("/opt/coding-request.json").read_text())
    output = Path(request["result_dir"])
    output.mkdir(parents=True, exist_ok=False)
    key = os.environ.pop("NEBIUS_API_KEY")
    model = request["model"]
    configuration = {
        "model": "tokenfactory/" + model,
        "small_model": "tokenfactory/" + model,
        "enabled_providers": ["tokenfactory"],
        "share": "disabled",
        "autoupdate": False,
        "permission": {"*": "allow", "question": "deny"},
        "tools": {"question": False, "websearch": False},
        "provider": {
            "tokenfactory": {
                "npm": "@ai-sdk/openai-compatible",
                "name": "Token Factory",
                "options": {"baseURL": request["base_url"], "apiKey": "{env:NEBIUS_API_KEY}"},
                "models": {model: {"name": model}},
            }
        },
    }
    env = dict(
        os.environ,
        PATH="/usr/local/bin:/usr/bin:/bin",
        HOME="/root",
        NEBIUS_API_KEY=key,
        OPENCODE_CONFIG_CONTENT=json.dumps(configuration),
        OPENCODE_DISABLE_AUTOUPDATE="true",
        OPENCODE_DISABLE_DEFAULT_PLUGINS="true",
        OPENCODE_DISABLE_PROJECT_CONFIG="true",
        OPENCODE_DISABLE_MODELS_FETCH="true",
    )
    result = {"status": "failed", "answer": "", "error": None, "opencode_version": None}
    try:
        version = subprocess.check_output(
            ["/usr/local/bin/opencode", "--version"], text=True, timeout=10
        ).strip()
        result["opencode_version"] = version
        if version != request["opencode_version"]:
            raise RuntimeError("Runtime image has a different OpenCode version")
        exit_code = run_agent(
            [
                "/usr/local/bin/opencode",
                "run",
                "--format",
                "json",
                "--agent",
                "build",
                "--auto",
                request["task"],
            ],
            cwd=WORKSPACE,
            env=env,
            output=output,
            key=key,
            timeout=max(1, request["timeout"] - 30),
            live=request.get("stream", False),
        )
        final_step = None
        failure = None
        for line in (output / "events.jsonl").read_text().splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            part = event.get("part", {})
            if event.get("type") == "text":
                result["answer"] = part.get("text", "")
            elif event.get("type") == "step_finish":
                final_step = part.get("reason")
            elif event.get("type") == "error":
                failure = "OpenCode reported an error; see events.jsonl"
        if exit_code == 0 and final_step == "stop" and result["answer"] and not failure:
            result["status"] = "completed"
        else:
            result["error"] = failure or "OpenCode did not produce a successful final response"
    except subprocess.TimeoutExpired:
        result.update(status="timed_out", error="Agent exceeded its deadline")
    except Exception as exc:
        result["error"] = f"Agent execution failed: {type(exc).__name__}"
    finally:
        # Configuration contains an environment reference, never the actual key.
        # Redact accidental echoes from diagnostic text before publishing it.
        for name in ("events.jsonl", "stderr.log"):
            path = output / name
            if path.exists():
                path.write_text(path.read_text(errors="replace").replace(key, "[REDACTED]"))
        result["answer"] = result["answer"].replace(key, "[REDACTED]")
        try:
            with tarfile.open(output / "workspace.tar.gz", "w:gz") as archive:
                archive.add(WORKSPACE, arcname="workspace")
        except Exception as exc:
            result.update(status="failed", error=f"Workspace archive failed: {type(exc).__name__}")
        (output / "result.json").write_text(json.dumps(result, indent=2))
        print(json.dumps({"status": result["status"]}), flush=True)


if __name__ == "__main__":
    main()

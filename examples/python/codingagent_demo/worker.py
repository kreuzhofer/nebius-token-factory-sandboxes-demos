"""Execute the pinned agent inside a task sandbox and publish its result files."""

import json
import os
import signal
import subprocess
import tarfile
from pathlib import Path

WORKSPACE = Path("/workspace")


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
        with (
            (output / "events.jsonl").open("w") as events,
            (output / "stderr.log").open("w") as errors,
        ):
            process = subprocess.Popen(
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
                stdin=subprocess.DEVNULL,
                stdout=events,
                stderr=errors,
                start_new_session=True,
            )
            try:
                exit_code = process.wait(timeout=max(1, request["timeout"] - 30))
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                raise
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

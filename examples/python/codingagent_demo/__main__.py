"""Build an OpenCode image once, then run unattended coding tasks in fresh sandboxes."""

import argparse
import json
from pathlib import Path

from configuration import SandboxConfig, load_env
from nebius_sandbox import SandboxClient

from . import AgentConfig, build_runtime, run_task
from .deadline import run_deadline_probe
from .examples import STAGES, run_example, run_ladder
from .job import monitor_task
from .proof import run_proof
from .runtime import MODEL


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser(
        "build-image", help="Build a reusable runtime without inference credentials"
    )
    build.add_argument(
        "--base-image", help="Existing Python 3.12 image; otherwise import python:3.12-slim"
    )
    build.add_argument("--output", required=True, type=Path)
    run = commands.add_parser("run", help="Run one task using an existing runtime image")
    image = run.add_mutually_exclusive_group(required=True)
    image.add_argument("--image", help="Prepared image UUID")
    image.add_argument("--runtime", type=Path, help="runtime.json from build-image")
    task = run.add_mutually_exclusive_group(required=True)
    task.add_argument("--task")
    task.add_argument("--task-file", type=Path)
    run.add_argument(
        "--file",
        action="append",
        type=Path,
        default=[],
        help="Input file or folder; repeat to add more",
    )
    run.add_argument("--model")
    run.add_argument("--timeout", type=int, default=300)
    run.add_argument("--output", required=True, type=Path)
    run.add_argument("--stream", action="store_true", help="Show live agent activity")
    monitor = commands.add_parser("monitor", help="Resume an existing task without submitting work")
    monitor.add_argument(
        "--output", required=True, type=Path, help="Existing task output directory"
    )
    proof = commands.add_parser("proof", help="Edit/test two tasks and verify runtime-image reuse")
    proof_image = proof.add_mutually_exclusive_group(required=True)
    proof_image.add_argument("--image")
    proof_image.add_argument("--runtime", type=Path)
    proof.add_argument("--model")
    proof.add_argument("--output", required=True, type=Path)
    example = commands.add_parser("example", help="Run a coding example with independent checks")
    example.add_argument("stage", choices=[*STAGES, "deadline", "all"])
    example_image = example.add_mutually_exclusive_group(required=True)
    example_image.add_argument("--image")
    example_image.add_argument("--runtime", type=Path)
    example.add_argument("--model")
    example.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    values = load_env(args.env_file)
    config = SandboxConfig.from_env(values)
    client = SandboxClient(config.token, config.project, config.base_url)
    if args.command == "monitor":
        result = monitor_task(client, args.output)
        print(json.dumps(result, indent=2))
        if result["status"] != "completed":
            raise SystemExit(1)
        return
    if args.command == "build-image":
        image = build_runtime(client, args.output, base_image=args.base_image)
        print(f"Runtime image: {image}")
        return
    image = args.image or json.loads(args.runtime.read_text())["image"]
    if not image:
        raise ValueError("Runtime build has no completed image")
    if args.command == "example" and args.stage == "deadline":
        result = run_deadline_probe(client, image, args.output)
        print(json.dumps(result, indent=2))
        if not result["passed"]:
            raise SystemExit(1)
        return
    key = values.get("NEBIUS_API_KEY")
    if not key:
        raise ValueError("Set NEBIUS_API_KEY for Token Factory inference")
    inference = AgentConfig(
        key,
        args.model or values.get("NEBIUS_CODING_MODEL") or MODEL,
        values.get("NEBIUS_BASE_URL") or "https://api.tokenfactory.nebius.com/v1",
    )
    if args.command in {"proof", "example"}:
        if args.command == "proof":
            result = run_proof(client, inference, image, args.output)
        elif args.stage == "all":
            result = run_ladder(client, inference, image, args.output)
        else:
            result = run_example(client, inference, image, args.stage, args.output)
        print(json.dumps(result, indent=2))
        if not result["passed"]:
            raise SystemExit(1)
        return
    result = run_task(
        client,
        inference,
        image,
        args.task or args.task_file.read_text(),
        files=args.file,
        output=args.output,
        timeout=args.timeout,
        stream=args.stream,
    )
    print(json.dumps(result, indent=2))
    if result["status"] != "completed":
        raise SystemExit(1)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}")
        raise SystemExit(1) from None

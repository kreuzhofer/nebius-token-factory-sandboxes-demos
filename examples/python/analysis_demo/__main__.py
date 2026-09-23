"""Prepare a runtime, analyze CSVs, and continue from the resulting checkpoint."""

import argparse
import json
from pathlib import Path

from codingagent_demo import AgentConfig
from codingagent_demo.runtime import MODEL
from configuration import SandboxConfig, load_env
from nebius_sandbox import SandboxClient

from .run import FOLLOWUP_TASK, INITIAL_TASK, run_analysis
from .runtime import build_analysis_runtime


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--runtime", type=Path, help="Reuse a prepared analysis runtime.json")
    parser.add_argument(
        "--file", action="append", type=Path, help="Sales CSV; repeat for multiple files"
    )
    parser.add_argument("--task", default=INITIAL_TASK)
    parser.add_argument("--followup", default=FOLLOWUP_TASK)
    parser.add_argument("--model")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument(
        "--fail-followup", action="store_true", help="Demonstrate a controlled failed child"
    )
    args = parser.parse_args()
    values = load_env(args.env_file)
    sandbox = SandboxConfig.from_env(values)
    key = values.get("NEBIUS_API_KEY")
    if not key:
        raise ValueError("Set NEBIUS_API_KEY for Token Factory inference")
    config = AgentConfig(
        key,
        args.model or values.get("NEBIUS_CODING_MODEL") or MODEL,
        values.get("NEBIUS_BASE_URL") or "https://api.tokenfactory.nebius.com/v1",
    )
    client = SandboxClient(sandbox.token, sandbox.project, sandbox.base_url)
    args.output.mkdir(parents=True, exist_ok=False)
    image = (
        json.loads(args.runtime.read_text())["image"]
        if args.runtime
        else build_analysis_runtime(client, args.output / "runtime")
    )
    if not image:
        raise ValueError("Analysis runtime has no completed image")
    result = run_analysis(
        client,
        config,
        image,
        args.output / "run",
        files=args.file,
        task=args.task,
        followup=args.followup,
        timeout=args.timeout,
        fail_followup=args.fail_followup,
    )
    print(json.dumps(result, indent=2))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}")
        raise SystemExit(1) from None

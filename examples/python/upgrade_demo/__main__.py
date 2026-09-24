"""Demonstrate a real dependency upgrade and independently verified rollback."""

import argparse
import json
from pathlib import Path

from codingagent_demo import AgentConfig, build_runtime
from codingagent_demo.runtime import MODEL
from configuration import SandboxConfig, load_env
from nebius_sandbox import SandboxClient

from .run import run_demo


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--runtime", type=Path, help="Reuse a coding runtime.json")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--model")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--scenario", choices=("both", "passing", "rollback"), default="both")
    args = parser.parse_args()
    if not 60 <= args.timeout <= 600:
        parser.error("--timeout must be between 60 and 600 seconds")
    values = load_env(args.env_file)
    sandbox = SandboxConfig.from_env(values)
    if not values.get("NEBIUS_API_KEY"):
        raise ValueError("Set NEBIUS_API_KEY for Token Factory inference")
    config = AgentConfig(
        values["NEBIUS_API_KEY"],
        args.model or values.get("NEBIUS_CODING_MODEL") or MODEL,
        values.get("NEBIUS_BASE_URL") or "https://api.tokenfactory.nebius.com/v1",
    )
    client = SandboxClient(sandbox.token, sandbox.project, sandbox.base_url)
    args.output.mkdir(parents=True, exist_ok=False)
    image = (
        json.loads(args.runtime.read_text())["image"]
        if args.runtime
        else build_runtime(client, args.output / "runtime")
    )
    if not image:
        raise ValueError("Runtime has no completed image")
    scenarios = ("passing", "rollback") if args.scenario == "both" else (args.scenario,)
    result = run_demo(
        client, config, image, args.output / "run", scenarios=scenarios, timeout=args.timeout
    )
    print(json.dumps(result, indent=2))
    return result["exit_code"]


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}")
        raise SystemExit(1) from None

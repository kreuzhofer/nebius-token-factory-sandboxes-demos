# Basic examples for Nebius Token Factory Sandboxes

Run these commands from `examples/python/`. See the [shared development setup](../README.md#development).


### Setup

Clone the repository and enter it:

```sh
git clone https://github.com/kreuzhofer/nebius-token-factory-sandboxes-demos.git
cd nebius-token-factory-sandboxes-demos/examples/python
```

You need an API key and a Project ID with access to Nebius Token Factory
Sandboxes. The agent example also needs access to a Token Factory model that
supports tool calling.


```sh
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 -m basic_demo configure
python3 -m basic_demo images
python3 -m basic_demo smoke
python3 -m basic_demo models
# Set NEBIUS_MODEL in .env to an available model supporting chat tool calling.
python3 -m basic_demo agent --image IMAGE_UUID_PRINTED_BY_SMOKE
```

`configure` uses hidden prompts for tokens and creates a gitignored `.env` with mode 0600. Alternatively set the variables in `basic_demo/.env.example` in your environment. The sandbox token falls back to NEBIUS_API_KEY; the Project header comes from CONTREE_PROJECT. Sandbox and inference credentials can differ. The public sandbox API documents bearer authentication plus a Project header; sandbox access must be enabled for the selected project.

Without `--image` or CONTREE_IMAGE, a run imports `docker.io/library/python:3.12-slim` privately, then prints the resulting image UUID for reuse. An existing image must contain `/usr/local/bin/python3`. `images` displays the first 100 public images; it does not guarantee those images include Python. `models` lists available inference IDs; tool support needs live verification. `all` runs both stages, requiring inference settings up front.

## What runs where

Local basic_demo → sandbox HTTPS API → OCI Python filesystem in a microVM → basic_demo/agent.py → Token Factory chat completions → Python subprocess tool inside the microVM.

* `smoke`: networking disabled; computes and verifies a sum of squares, writes `/tmp/smoke.json`, and returns stdout. A checkpoint is retained.
* `agent`: networking enabled; asks a model to calculate primes using a Python tool, sends tool results back, and prints the final answer. Limited to five inference turns, 1,200 output tokens per turn, 15 seconds per Python tool, and 360 seconds of sandbox execution. This run is disposable and does not preserve environment variables.

Commands are submitted as operations. Each execution gets its own VM; this example does not keep a long-lived VM between stages. Filesystem checkpointing is distinct from keeping a live Python process. The import and smoke checkpoints are subject to service retention. No persistent tag is created.

The inference key is passed in the agent execution environment, removed from the agent's environment before tools run, and excluded from subprocess environments. It is still sent to the sandbox service as request metadata; disposable execution is not a guarantee of metadata deletion. Use a scoped test key. This tiny agent is an execution demonstration, not a hardened adversarial agent framework.

### Sandbox SDK

The shared adapter uses `contree-sdk==0.3.6`, `contree-client[httpx]==0.4.0`,
and `httpx==0.28.1`. It uses the SDK for account limits and checkpoint reads,
and the official low-level client where the stable SDK cannot preserve the
existing contract. Submissions are sent once, operation IDs return immediately,
and local polling deadlines remain separate from server execution deadlines.
See [SDK compatibility](../../../docs/sandbox-sdk.md) for the verified constructor,
retry behavior, and capability gaps.

Sources:

- [Sandbox overview](https://docs.tokenfactory.nebius.com/sandboxes/overview)
- [SDK setup](https://docs.tokenfactory.nebius.com/sandboxes/sdk/python_sdk/getting-started)
- [SDK release](https://pypi.org/project/contree-sdk/)
- [Generated client release](https://pypi.org/project/contree-client/)
- [Authentication](https://docs.tokenfactory.nebius.com/sandboxes/cli/tutorial/installation)
- [Spawn API](https://docs.tokenfactory.nebius.com/api-reference/sandboxes/instances/spawn-a-new-container-instance)
- [Operation status](https://docs.tokenfactory.nebius.com/api-reference/sandboxes/operations/get-an-operation-status)
- [Inference quickstart](https://docs.tokenfactory.nebius.com/quickstart)


To check the standard-library smoke example locally:

```sh
python3 -m basic_demo.agent smoke
```

The receipt workflow and the small prime-number tool demo share `../nebius_sandbox.py`. Other language implementations own their own
sandbox and agent code.

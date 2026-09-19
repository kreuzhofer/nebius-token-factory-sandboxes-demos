# Python sandbox SDK compatibility

Checked against installed packages on September 19, 2026:

| Package | Pinned version | Python requirement |
| --- | --- | --- |
| `contree-sdk` | `0.3.6` | `>=3.10,<3.15` |
| `contree-client[httpx]` | `0.4.0` | `>=3.10` |
| `httpx` | `0.28.1` | `>=3.8` |

The shared `SandboxClient` adapter uses these official packages. Install the
shared Python requirements before running a launcher. Receipt workers receive
the same requirements and install them with their agent dependencies before
importing the provider. Development requirements include these pins through the
receipt requirements. The basic sandbox script and coding runtime do not import
the provider and do not need the SDK themselves.

## Constructor and configuration

The published stable SDK accepts
`ContreeSync(config=None, *, base_url=None, token=None)`. The current
[official client reference](https://docs.tokenfactory.nebius.com/sandboxes/sdk/python_sdk/reference/client)
describes injecting a `contree_client` transport instead. That constructor does
not apply to the pinned stable release. The installed SDK uses its own HTTPX
transport; the separately installed official client supplies the fallbacks below.

The adapter constructs `ContreeConfig` explicitly. Its `IAMAuth` specialization
uses the already resolved configuration literally, preventing the SDK from
reinterpreting tokens as environment-variable names or consulting an unrelated
auth profile. An absent project produces no Project header. Existing
`CONTREE_TOKEN`/`NEBIUS_API_KEY` precedence stays in `SandboxConfig`.

Callers may keep the existing sandbox base URL ending in `/v1`. The adapter
removes that suffix because both packages append it, retaining any preceding
path such as `/sandboxes`. HTTPS remains required. HTTP requests retain a
30-second transport timeout.

## Capability mapping and remaining gaps

| Shared capability | Official API used | Reason for fallback where applicable |
| --- | --- | --- |
| Account limits | SDK `get_token_info(refresh=True)` | Return only limits, without token identity. |
| Checkpoint reads | SDK `images.use(image).read(path)` | Read bytes from the retained filesystem without creating a session or running another command. |
| Image listing | Client `list_images` | Stable SDK lists image objects without creation metadata or the caller's offset pagination. |
| Image import | Client `import_image` | Stable SDK retries submissions, assigns a tag, and waits internally. The adapter must return the operation ID immediately, avoid creating tags, and keep deadlines separate. |
| In-memory upload | Client `upload_file` | Stable SDK exposes `files.upload(path)` but no public bytes-upload method. Preserve byte uploads without staging local files, then verify SHA-256 and retain the requested mode. |
| Command submission | Client `spawn_instance` | Stable SDK's run/wait lifecycle retries some submissions and does not expose the required independent submission/status contract. Preserve explicit environment, networking, disposable state, files, output/layer limits, and immediate operation identity. |
| Operation status/cancellation | Client `get_operation_status` / `cancel_operation` | Stable SDK exposes these through its internal combined lifecycle rather than independent public operations. |

All fallbacks stay inside the shared adapter and use public official-client
methods and models. No hand-written sandbox HTTP transport remains. The model
discovery transport and inference transports remain separate.

`Operation` still distinguishes failed/cancelled operations from unsuccessful
processes; `ExecutionResult` retains exit status, timeout, signal, and decoded
output. Truncated output is rejected. Artifact callers still require a retained
image, while disposable executions can succeed without one.

## Retries and errors

Inspection of SDK `0.3.6` found that its operation starter uses `CircuitRetrier`
for `ApiTimeoutError` and `TooManyRequestsError`, with no public retry-off option.
The adapter therefore never uses SDK run/import submission. The official client
is constructed with `retry=None`, which sends each request once, including
HTTP 429 responses. No operation is resubmitted after an ambiguous failure.

The adapter retains its monotonic polling deadline, status-change callbacks,
checked/unchecked results, and cancellation on local timeout or interruption.
Unconfirmed cancellation warns and preserves the original exception; server
execution timeouts remain active. Connection failure while polling does not
cancel or resubmit a known operation.

Both packages' HTTP errors become the existing `TransportError`, with status
codes but no response bodies or exception chains in displayed diagnostics. The
stable SDK sometimes omits the HTTP status when it is absent from the JSON error
body; the adapter recovers the status from its underlying HTTP exception.

## Validation

Contract tests use the real installed packages with controlled HTTP responses.
They cover configuration, single submission, execution options, image import and
listing, upload integrity, polling/cancellation, result decoding, and checkpoint
reads. Receipt packaging and coding task/outcome tests exercise the consumers.

Live validation results and any remaining compatibility limitations are recorded
on [issue #36](https://github.com/kreuzhofer/nebius-token-factory-sandboxes-demos/issues/36).
Live IDs, credentials, and generated results stay outside tracked files.

Release references: [SDK release](https://pypi.org/project/contree-sdk/0.3.6/),
[official client release](https://pypi.org/project/contree-client/0.4.0/),
and [SDK source](https://github.com/nebius/contree-sdk).

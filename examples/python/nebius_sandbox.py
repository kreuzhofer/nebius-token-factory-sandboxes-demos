"""Nebius Token Factory Sandboxes operations, independent of any demo or agent workflow.

Submission is never retried. A successful operation does not imply a successful
process: callers inspect execution_result() before consuming execution output.
"""

from __future__ import annotations

import base64
import hashlib
import time
import warnings
from contextlib import contextmanager
from dataclasses import dataclass, field

import sandbox_events
from contree_client.exceptions import APIConnectionError, APIStatusError
from contree_client.httpx import ContreeClient
from contree_client.models import (
    ClosableStreamRepr,
    FileSpec,
    ImageImportRegistry,
    InstanceNetworking,
    InstanceResourcesLimits,
)
from contree_sdk import ContreeSync
from contree_sdk.auth import IAMAuth
from contree_sdk.config import ContreeConfig
from contree_sdk.sdk.exceptions.api import ApiStatusCodeError, ContreeApiError
from http_transport import TransportError
from httpx import HTTPStatusError


class _ExplicitAuth(IAMAuth):
    """Configuration is already resolved by our entrypoints, including absent project."""

    def resolve(self):
        return self

    def get_headers(self):
        headers = {"Authorization": f"Bearer {self.token}"}
        if self.project_id:
            headers["Project"] = self.project_id
        return headers


@contextmanager
def _provider_errors(method):
    """Keep provider exception bodies and credentials out of caller diagnostics."""
    try:
        yield
    except (APIStatusError, ApiStatusCodeError) as exc:
        # SDK 0.3.6 reads status from the JSON body, where it can be absent.
        status = exc.status
        if isinstance(exc.__cause__, HTTPStatusError):
            status = exc.__cause__.response.status_code
        raise TransportError(f"{method} request failed: HTTP {status}") from None
    except (APIConnectionError, ContreeApiError):
        raise TransportError(f"{method} request failed: connection unavailable") from None


class OperationFailed(RuntimeError):
    """A known operation reached a failed or cancelled terminal state."""


class ExecutionFailed(RuntimeError):
    """An operation succeeded, but its sandbox process failed."""


@dataclass(frozen=True)
class ExecutionResult:
    stdout: str
    stderr: str
    image: str | None
    exit_code: int | None = None
    timed_out: bool = False
    signal: int | None = None
    output_truncated: bool = False

    @property
    def successful(self):
        return self.exit_code == 0 and not self.timed_out and self.signal in (None, -1, 0)


def _decode(stream, *, allow_truncated=False):
    if stream.get("truncated") and not allow_truncated:
        raise RuntimeError("Sandbox output was truncated")
    value = stream.get("value", "")
    return (
        base64.b64decode(value).decode(errors="replace" if allow_truncated else "strict")
        if stream.get("encoding") == "base64"
        else value
    )


@dataclass(frozen=True)
class Operation:
    id: str
    status: str
    image: str | None = None
    _result: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_response(cls, operation_id, payload):
        return cls(
            operation_id,
            payload["status"],
            payload.get("result_image_uuid") or (payload.get("result") or {}).get("image"),
            (payload.get("metadata") or {}).get("result") or {},
        )

    @property
    def done(self):
        return self.status in ("SUCCESS", "FAILED", "CANCELLED")

    def require_success(self):
        if self.status in ("FAILED", "CANCELLED"):
            raise OperationFailed(f"Operation {self.id}: {self.status}")
        if self.status != "SUCCESS":
            raise RuntimeError(f"Operation {self.id} is not complete")

    def require_image(self):
        """Return a completed operation's retained image, including image imports."""
        self.require_success()
        if not self.image:
            raise RuntimeError(f"Operation {self.id} returned no filesystem image")
        return self.image

    def execution_result(self, *, require_image=False, check=True, allow_truncated=False):
        """Read output from a successful operation; check=False retains failed process state.

        require_image=True additionally requires a filesystem for artifact retrieval.
        """
        self.require_success()
        state = self._result.get("state") or {}
        if check and (
            state.get("timed_out")
            or state.get("signal", -1) not in (None, -1, 0)
            or state.get("exit_code") != 0
        ):
            raise ExecutionFailed(f"Sandbox process {self.id} failed")
        return ExecutionResult(
            _decode(self._result.get("stdout") or {}, allow_truncated=allow_truncated),
            _decode(self._result.get("stderr") or {}, allow_truncated=allow_truncated),
            self.require_image() if require_image else self.image,
            state.get("exit_code"),
            bool(state.get("timed_out")),
            state.get("signal"),
            any((self._result.get(name) or {}).get("truncated") for name in ("stdout", "stderr")),
        )


class SandboxClient:
    def operation_events(self, operation_id, *, after=None, deadline=None):
        """Read one event stream; a caller acknowledges events by persisting their IDs."""
        yield from sandbox_events.operation_events(
            self._client,
            operation_id,
            after=after,
            deadline=deadline,
        )

    def wait_with_events(self, operation_id, seconds, *, after=None, on_event, on_warning):
        return sandbox_events.wait_with_events(
            self,
            operation_id,
            seconds,
            after=after,
            on_event=on_event,
            on_warning=on_warning,
        )

    def __init__(self, token, project=None, base_url=None):
        base_url = (base_url or "https://api.tokenfactory.nebius.com/sandboxes/v1").rstrip("/")
        if not base_url.startswith("https://"):
            raise ValueError("HTTPS required")
        # Both official packages append /v1; callers historically include it.
        self._client = ContreeClient(
            token, project=project, base_url=base_url.removesuffix("/v1"), timeout=30, retry=None
        )
        self._sdk = ContreeSync(
            ContreeConfig(
                auth=_ExplicitAuth(
                    token=token, project_id=project or "", base_url=base_url.removesuffix("/v1")
                ),
                transport_timeout=30,
            )
        )

    def list_images(self, *, limit=100, offset=0):
        # The SDK image objects omit listing metadata and offset pagination.
        with _provider_errors("GET"):
            return self._client.list_images(limit=limit, offset=offset).to_dict()

    def limits(self):
        """Return the configured token limits without exposing token identity or credentials."""
        with _provider_errors("GET"):
            return self._sdk.get_token_info(refresh=True).limits

    def import_image(self, registry_url, *, timeout=300):
        # SDK imports retry submissions, assign tags, and wait internally.
        with _provider_errors("POST"):
            return self._client.import_image(ImageImportRegistry(url=registry_url), timeout=timeout)

    def import_image_and_wait(self, registry_url, *, timeout=300, wait_timeout=360):
        """Import a caller-selected runtime and return its image after successful completion.

        Use import_image() and wait() separately when the operation ID is needed
        before completion. Execution and local polling deadlines are independent.
        """
        operation_id = self.import_image(registry_url, timeout=timeout)
        return self.wait(operation_id, wait_timeout).require_image()

    def upload(self, data, *, mode="0600"):
        # The stable SDK exposes upload(path), but no public in-memory upload.
        with _provider_errors("POST"):
            result = self._client.upload_file(data)
        if result.sha256 != hashlib.sha256(data).hexdigest():
            raise RuntimeError("Uploaded file checksum mismatch")
        return {"uuid": result.uuid, "mode": mode}

    def download(self, image, path):
        with _provider_errors("GET"):
            return self._sdk.images.use(image).read(path)

    def submit(
        self,
        image,
        *,
        command,
        args=(),
        stdin=None,
        cwd=None,
        files=None,
        env=None,
        timeout=300,
        disposable=False,
        networking=False,
        max_layer_bytes=268435456,
        output_limit=65536,
    ):
        """Submit a sandbox command once and return its operation ID immediately."""
        with _provider_errors("POST"):
            return self._client.spawn_instance(
                image=image,
                command=command,
                args=list(args),
                files={path: FileSpec(**ref) for path, ref in (files or {}).items()},
                env=env or {},
                preserve_env=False,
                disposable=disposable,
                networking=InstanceNetworking(enabled=networking),
                timeout=timeout,
                resources_limits=InstanceResourcesLimits(max_layer_bytes=max_layer_bytes),
                truncate_output_at=output_limit,
                stdin=ClosableStreamRepr(value=stdin, encoding="ascii", close=True)
                if stdin is not None
                else ...,
                cwd=cwd if cwd is not None else ...,
            ).uuid

    def get_operation(self, operation_id):
        with _provider_errors("GET"):
            payload = self._client.get_operation_status(operation_id).to_dict()
        return Operation.from_response(operation_id, payload)

    def cancel(self, operation_id):
        with _provider_errors("DELETE"):
            self._client.cancel_operation(operation_id)

    def wait(self, operation_id, seconds, *, check=True, on_status=None):
        """Poll to completion; deadlines/interrupts cancel, connection failures do not resubmit.

        check=False returns failed/cancelled operations for callers that report
        their own outcomes. on_status receives each change in operation status.
        """
        deadline = time.monotonic() + seconds
        previous = None
        try:
            while time.monotonic() < deadline:
                operation = self.get_operation(operation_id)
                if on_status and operation.status != previous:
                    on_status(operation.status)
                    previous = operation.status
                if operation.done:
                    if check:
                        operation.require_success()
                    return operation
                time.sleep(1)
            raise TimeoutError(f"Operation {operation_id} exceeded local deadline")
        except (KeyboardInterrupt, TimeoutError):
            try:
                self.cancel(operation_id)
            except Exception:
                warnings.warn(
                    f"Cancellation unconfirmed: {operation_id}; server timeout remains active",
                    RuntimeWarning,
                    stacklevel=2,
                )
            raise

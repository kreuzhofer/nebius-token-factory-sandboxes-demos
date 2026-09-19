"""Inference model discovery transport and the shared safe transport-error type."""

import json
import urllib.error
import urllib.request


class TransportError(RuntimeError):
    """A request failed; messages never include credentials or response bodies."""


class HttpTransport:
    def __init__(self, base_url, token, project=None):
        self.base_url = base_url.rstrip("/")
        if not self.base_url.startswith("https://"):
            raise ValueError("HTTPS required")
        self.headers = {"Authorization": "Bearer " + token}
        if project:
            self.headers["Project"] = project

    def request(self, method, path, body=None):
        raw = self.transfer(method, path, None if body is None else json.dumps(body).encode())
        return json.loads(raw) if raw else {}

    def transfer(self, method, path, data=None, content_type="application/json"):
        """Send once. A failed submission may already have created an operation."""
        headers = dict(self.headers, **{"Content-Type": content_type})
        request = urllib.request.Request(
            self.base_url + path, method=method, headers=headers, data=data
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            raise TransportError(f"{method} request failed: HTTP {exc.code}") from None
        except (urllib.error.URLError, OSError):
            raise TransportError(f"{method} request failed: connection unavailable") from None

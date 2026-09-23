"""Shared event-stream transport and bounded observation of an existing operation."""

import time
import warnings
from contextlib import closing

from contree_client.runtime import parse_retry_after
from http_transport import TransportError
from httpx import HTTPError, HTTPStatusError


class EventStreamError(TransportError):
    def __init__(self, message, *, retryable=False, retry_after=None):
        super().__init__(message)
        self.retryable = retryable
        self.retry_after = retry_after


def operation_events(api, operation_id, *, after=None, deadline=None):
    try:
        with closing(
            api.iter_operation_events(
                operation_id,
                follow=True,
                last_event_id=after,
                deadline=deadline,
            )
        ) as source:
            for event in source:
                value = event.to_dict()
                if type(value.get("id")) is not int or value["id"] < 0:
                    raise ValueError("Invalid event identity")
                if not isinstance(value.get("type"), str) or not isinstance(
                    value.get("data"), dict
                ):
                    raise ValueError("Invalid event payload")
                yield value
    except HTTPStatusError as exc:
        status = exc.response.status_code
        raise EventStreamError(
            f"Event stream failed: HTTP {status}",
            retryable=status in {410, 425, 429, 502, 504},
            retry_after=parse_retry_after(exc.response.headers.get("Retry-After")),
        ) from None
    except (HTTPError, ConnectionError):
        raise EventStreamError("Event stream connection interrupted", retryable=True) from None
    except (ValueError, TypeError, KeyError, AttributeError):
        raise EventStreamError("Invalid event stream payload") from None


def wait_with_events(client, operation_id, seconds, *, after=None, on_event, on_warning):
    """Deliver before acknowledging; reconnect at most five times without progress."""
    deadline = time.monotonic() + max(0, seconds)
    operation = None
    try:
        operation = client.get_operation(operation_id)
        if operation.done and seconds <= 0:
            on_warning("Original deadline elapsed; live transcript was not drained")
            return operation
        failures = 0
        while time.monotonic() < deadline:
            try:
                with closing(
                    client.operation_events(operation_id, after=after, deadline=deadline)
                ) as events:
                    for event in events:
                        if after is not None and event["id"] <= after:
                            continue
                        on_event(event)
                        after = event["id"]
                        failures = 0
                        if event["type"] == "completion":
                            operation = client.get_operation(operation_id)
                            if operation.done:
                                return operation
                            break
                    else:
                        raise EventStreamError(
                            "Event stream closed before completion", retryable=True
                        )
                break
            except EventStreamError as exc:
                if not exc.retryable:
                    raise
                failures += 1
                if failures >= 5:
                    on_warning(
                        "Live transcript may be incomplete: stream retry limit reached; polling status"
                    )
                    break
                delay = (
                    exc.retry_after if exc.retry_after is not None else min(2 ** (failures - 1), 10)
                )
                remaining = max(0, deadline - time.monotonic())
                if delay >= remaining:
                    on_warning(
                        "Live transcript may be incomplete: stream retry exceeds remaining deadline"
                    )
                    break
                time.sleep(delay)
        else:
            raise TimeoutError("Operation monitoring deadline reached")
    except (KeyboardInterrupt, TimeoutError) as exc:
        if isinstance(exc, TimeoutError) and operation is not None:
            if not operation.done:
                try:
                    operation = client.get_operation(operation_id)
                except (TransportError, TimeoutError):
                    pass
            if operation.done:
                on_warning("Live transcript may be incomplete: stream deadline reached")
                return operation
        try:
            client.cancel(operation_id)
        except Exception:
            warnings.warn(
                f"Cancellation unconfirmed: {operation_id}; server timeout remains active",
                RuntimeWarning,
                stacklevel=2,
            )
        raise
    # The existing polling path owns cancellation once fallback begins.
    return client.wait(operation_id, max(0, deadline - time.monotonic()), check=False)

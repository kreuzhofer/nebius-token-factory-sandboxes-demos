"""Operation streaming contracts at the real client's HTTP boundary."""

import json
import time
import unittest
from unittest.mock import patch

import httpx
from nebius_sandbox import SandboxClient


def frame(event_id, kind, data=None):
    event = {"id": event_id, "ts": "2026-09-23T00:00:00Z", "type": kind, "data": data or {}}
    return f"id: {event_id}\nevent: {kind}\ndata: {json.dumps(event)}\n\n".encode()


class Chunks(httpx.SyncByteStream):
    def __init__(self, *chunks):
        self.chunks = chunks

    def __iter__(self):
        for chunk in self.chunks:
            if isinstance(chunk, BaseException):
                raise chunk
            yield chunk


class EventTests(unittest.TestCase):
    def test_events_decode_split_sse_and_preserve_zero_and_resume_cursor(self):
        requests = []
        payload = b": keepalive\r\n\r\n" + frame(
            0, "stdout", {"encoding": "base64", "value": "aGkK"}
        )

        def respond(request):
            requests.append(request)
            return httpx.Response(200, stream=Chunks(payload[:31], payload[31:]), request=request)

        client = SandboxClient("secret", base_url="https://sandbox.example/v1")
        with patch("httpx.HTTPTransport.handle_request", side_effect=respond):
            events = list(client.operation_events("job", deadline=time.monotonic() + 5))
            list(client.operation_events("job", after=0, deadline=time.monotonic() + 5))
        self.assertEqual(events[0]["id"], 0)
        self.assertEqual(events[0]["type"], "stdout")
        self.assertNotIn("last-event-id", requests[0].headers)
        self.assertEqual(requests[1].headers["last-event-id"], "0")
        self.assertIn("follow=1", str(requests[0].url))

    def test_reconnect_resumes_after_delivered_event_and_drains_through_completion(self):
        requests = []
        streams = 0

        def respond(request):
            nonlocal streams
            requests.append(request)
            if request.url.path.endswith("/events"):
                streams += 1
                data = frame(0, "stdout", {"value": "first\n", "encoding": "ascii"}) + (
                    b"event: sse_error\ndata: secret must not escape\n\n"
                    if streams == 1
                    else frame(1, "exit", {"exit_code": 0})
                    + frame(2, "completion", {"status": "SUCCESS"})
                )
                return httpx.Response(200, stream=Chunks(data), request=request)
            return httpx.Response(
                200, json={"status": "SUCCESS" if streams == 2 else "EXECUTING"}, request=request
            )

        client = SandboxClient("secret", base_url="https://sandbox.example/v1")
        delivered = []
        warnings = []
        with patch("httpx.HTTPTransport.handle_request", side_effect=respond), patch("time.sleep"):
            result = client.wait_with_events(
                "job", 30, on_event=delivered.append, on_warning=warnings.append
            )
        self.assertEqual(result.status, "SUCCESS")
        self.assertEqual([event["id"] for event in delivered], [0, 1, 2])
        self.assertEqual(
            [r.headers.get("last-event-id") for r in requests if r.url.path.endswith("/events")],
            [None, "0"],
        )
        self.assertFalse(any(r.method != "GET" for r in requests))
        self.assertNotIn("secret", str(warnings))

    def test_retryable_statuses_honor_retry_after_then_fall_back_without_resubmitting(self):
        for status in (410, 425, 429, 502, 504):
            requests = []
            sleeps = []

            def respond(request, requests=requests, status=status):
                requests.append(request)
                if request.url.path.endswith("/events"):
                    return httpx.Response(
                        status, headers={"Retry-After": "2"}, content=b"secret", request=request
                    )
                finished = sum(r.url.path.endswith("/events") for r in requests) == 5
                return httpx.Response(
                    200, json={"status": "SUCCESS" if finished else "EXECUTING"}, request=request
                )

            client = SandboxClient("secret", base_url="https://sandbox.example/v1")
            notices = []
            with (
                self.subTest(status=status),
                patch("httpx.HTTPTransport.handle_request", side_effect=respond),
                patch("time.sleep", side_effect=sleeps.append),
            ):
                result = client.wait_with_events(
                    "job", 60, on_event=lambda event: None, on_warning=notices.append
                )
            self.assertEqual(result.status, "SUCCESS")
            self.assertEqual(sleeps, [2, 2, 2, 2])
            self.assertIn("incomplete", notices[0])
            self.assertTrue(all(r.method == "GET" for r in requests))

    def test_permanent_errors_are_sanitized_and_never_retried(self):
        from sandbox_events import EventStreamError

        for status in (400, 401, 403, 404):
            requests = []

            def respond(request, requests=requests, status=status):
                requests.append(request)
                return httpx.Response(status, content=b"private response", request=request)

            client = SandboxClient("secret", base_url="https://sandbox.example/v1")
            with (
                self.subTest(status=status),
                patch("httpx.HTTPTransport.handle_request", side_effect=respond),
            ):
                with self.assertRaises(EventStreamError) as error:
                    list(client.operation_events("job", deadline=time.monotonic() + 5))
            self.assertFalse(error.exception.retryable)
            self.assertNotIn("private", str(error.exception))
            self.assertEqual(len(requests), 1)

    def test_interrupt_and_original_deadline_cancel_once(self):
        for timeout, failure in ((-1, None), (30, KeyboardInterrupt())):
            requests = []

            def respond(request, requests=requests, failure=failure):
                requests.append(request)
                if request.method == "DELETE":
                    return httpx.Response(200, request=request)
                if request.url.path.endswith("/events"):
                    return httpx.Response(200, stream=Chunks(failure), request=request)
                return httpx.Response(200, json={"status": "EXECUTING"}, request=request)

            client = SandboxClient("secret", base_url="https://sandbox.example/v1")
            with (
                self.subTest(timeout=timeout),
                patch("httpx.HTTPTransport.handle_request", side_effect=respond),
            ):
                with self.assertRaises(KeyboardInterrupt if failure else TimeoutError):
                    client.wait_with_events(
                        "job", timeout, on_event=lambda event: None, on_warning=lambda warning: None
                    )
            self.assertEqual(sum(r.method == "DELETE" for r in requests), 1)

    def test_stream_deadline_preserves_terminal_result_without_cancelling(self):
        for initially_done in (True, False):
            requests = []
            notices = []

            def respond(request, requests=requests, initially_done=initially_done):
                requests.append(request)
                if request.url.path.endswith("/events"):
                    return httpx.Response(200, stream=Chunks(TimeoutError()), request=request)
                done = initially_done or len(requests) > 1
                return httpx.Response(
                    200, json={"status": "SUCCESS" if done else "EXECUTING"}, request=request
                )

            client = SandboxClient("secret", base_url="https://sandbox.example/v1")
            with (
                self.subTest(initially_done=initially_done),
                patch("httpx.HTTPTransport.handle_request", side_effect=respond),
            ):
                result = client.wait_with_events(
                    "job", 30, on_event=lambda event: None, on_warning=notices.append
                )
                self.assertEqual(result.status, "SUCCESS")
                self.assertTrue(all(r.method == "GET" for r in requests))
                self.assertIn("incomplete", str(notices))

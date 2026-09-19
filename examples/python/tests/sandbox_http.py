"""Controlled service responses at the HTTP boundary, using the real SDK clients."""

from contextlib import contextmanager
from unittest.mock import patch

import httpx


@contextmanager
def sandbox_responses(*responses):
    pending = iter(responses)
    requests = []

    def respond(request):
        requests.append(request)
        value = next(pending)
        if isinstance(value, BaseException):
            raise value
        status, value = value if isinstance(value, tuple) else (200, value)
        if isinstance(value, bytes):
            return httpx.Response(status, content=value, request=request)
        return httpx.Response(status, json=value, request=request)

    def sync(request):
        request.read()
        return respond(request)

    async def asynchronous(request):
        await request.aread()
        return respond(request)

    with (
        patch("httpx.HTTPTransport.handle_request", side_effect=sync),
        patch("httpx.AsyncHTTPTransport.handle_async_request", side_effect=asynchronous),
    ):
        yield requests

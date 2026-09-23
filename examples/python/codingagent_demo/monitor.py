"""Durable task transcripts and readable presentation, independent of provider retries."""

import base64
import fcntl
import json
import os
import re
from contextlib import contextmanager
from pathlib import Path

from sandbox_events import EventStreamError


def save_json(path, value):
    path = Path(path)
    pending = path.with_suffix(path.suffix + ".tmp")
    with pending.open("w") as stream:
        json.dump(value, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    pending.replace(path)


@contextmanager
def task_lock(output):
    with (Path(output) / ".monitor.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another monitor already owns this task directory") from None
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def printable(text):
    # Keep untrusted process output from issuing terminal-control commands.
    return re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", str(text))


class Transcript:
    def __init__(self, output, record):
        self.path = Path(output) / "transcript.jsonl"
        self.cursor: int | None = None
        self.buffers = {}
        self.complete = False
        self.state = record.setdefault("monitoring", {"warnings": []})
        self.state["transcript"] = str(self.path)
        if self.path.exists():
            with self.path.open("rb+") as stream:
                while True:
                    position = stream.tell()
                    line = stream.readline()
                    if not line:
                        break
                    if not line.endswith(b"\n"):
                        # A torn final append has not acknowledged an event.
                        stream.truncate(position)
                        break
                    event = json.loads(line)
                    self.feed(event, display=False)
                    self.cursor = event["id"]
        self.update_state()

    def update_state(self):
        self.state.update(
            cursor=self.cursor,
            completion_seen=self.complete,
            complete=self.complete and not self.state["warnings"],
        )

    def warn(self, message):
        if message not in self.state["warnings"]:
            self.state["warnings"].append(message)
        self.update_state()
        print("Monitor warning: " + message, flush=True)

    def capture(self, event: dict):
        if self.cursor is not None and event["id"] <= self.cursor:
            return
        # Reject malformed output before acknowledging it in the durable journal.
        self.output_bytes(event)
        # The platform masks secret environment keys; do not persist environment values anyway.
        if "env" in event["data"]:
            event = {**event, "data": {**event["data"], "env": "[REDACTED]"}}
        with self.path.open("ab") as stream:
            stream.write(json.dumps(event).encode() + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        self.feed(event, display=True)
        self.cursor = event["id"]
        self.update_state()

    def feed(self, event, *, display):
        kind = event["type"]
        if self.cursor is not None and event["id"] > self.cursor + 1:
            message = (
                f"Live transcript is incomplete: missing events {self.cursor + 1}–{event['id'] - 1}"
            )
            if display:
                self.warn(message)
            elif message not in self.state["warnings"]:
                self.state["warnings"].append(message)
        if kind in {"truncated", "size_cap"}:
            message = "Live transcript is incomplete: sandbox output limit reached"
            if display:
                self.warn(message)
            elif message not in self.state["warnings"]:
                self.state["warnings"].append(message)
        elif kind == "completion":
            self.complete = True
            for key, pending in self.buffers.items():
                if pending and display:
                    self.show(key[1], pending)
            self.buffers.clear()
        if kind not in {"stdout", "stderr"}:
            if display and kind in {"init", "spawn", "exit", "shutdown", "completion"}:
                print("Sandbox event: " + kind, flush=True)
            return
        chunk = self.output_bytes(event)
        key = (event.get("spid"), kind)
        content = self.buffers.get(key, b"") + chunk
        lines = content.split(b"\n")
        self.buffers[key] = lines.pop()
        if len(self.buffers[key]) > 1024**2:
            self.buffers[key] = b""
            if display:
                print("[Large unfinished message retained in transcript]", flush=True)
        if display:
            for line in lines:
                self.show(kind, line)

    @staticmethod
    def output_bytes(event):
        if event["type"] not in {"stdout", "stderr"}:
            return b""
        data = event["data"]
        try:
            if data["encoding"] == "base64":
                return base64.b64decode(data["value"], validate=True)
            elif data["encoding"] == "ascii":
                return data["value"].encode()
            else:
                raise ValueError("Unknown encoding")
        except (ValueError, TypeError, KeyError, AttributeError):
            raise EventStreamError("Invalid stdout/stderr event payload") from None

    @staticmethod
    def show(kind, line):
        text = line.decode("utf-8", errors="replace")
        if kind == "stdout":
            try:
                event = json.loads(text)
            except ValueError:
                event = None
            if isinstance(event, dict):
                part = event.get("part")
                part = part if isinstance(part, dict) else {}
                if event.get("type") == "text":
                    if not str(part.get("text", "")).strip():
                        return
                    text = "Agent: " + str(part.get("text", ""))
                elif event.get("type") == "tool_use":
                    state = part.get("state")
                    state = state if isinstance(state, dict) else {}
                    text = f"Tool: {part.get('tool', 'unknown')} [{state.get('status', '')}] {state.get('title', '')}"
                elif event.get("type") == "error":
                    text = "Agent reported an error; see transcript"
                elif event.get("status"):
                    text = "Worker: " + str(event["status"])
                else:
                    return
        if text:
            print(printable(("stderr: " if kind == "stderr" else "") + text), flush=True)

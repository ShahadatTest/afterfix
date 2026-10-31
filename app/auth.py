"""Phase A1/A2: fail-closed bearer auth, authorization, and bounded bodies.

Limitations: single demo instance with one operator and one optional viewer
credential configured via environment; not an identity platform and not
multi-tenant. Actor IDs here are the configured operator/viewer identities.
"""
import asyncio
import hmac
import math
import re
import threading
import time
from collections import deque
from dataclasses import dataclass

MAX_BODY_BYTES = 65536
BODY_READ_TIMEOUT_SECONDS = 5.0
_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{32,128}")
_PUBLIC_READ_PATHS = frozenset({
    "/auth/me", "/lab/scenarios", "/incidents/INC-001",
    "/incidents/INC-001/evidence", "/incidents/INC-001/patch",
    "/incidents/INC-001/cases",
})
_PUBLIC_REPORT = re.compile(r"/cases/CASE-[0-9a-f]{16}/report")


def public_read_allowed(method, path):
    """Explicit opt-in for the owner's public synthetic demo, GET only."""
    import os
    return (os.environ.get("AFTERFIX_AGENT_MODE") != "1"
            and os.environ.get("AFTERFIX_PUBLIC_VIEWER") == "1"
            and method == "GET"
            and (path in _PUBLIC_READ_PATHS or _PUBLIC_REPORT.fullmatch(path) is not None))


@dataclass(frozen=True)
class Actor:
    actor_id: str
    role: str  # fixed as "operator" or "viewer" from the matched token


def _valid_token(value):
    return isinstance(value, str) and bool(_TOKEN_RE.fullmatch(value))


def _load_tokens():
    """Return (operator, viewer_or_None), or None for any invalid config."""
    import os

    operator = os.environ.get("AFTERFIX_OPERATOR_TOKEN")
    viewer_present = "AFTERFIX_VIEWER_TOKEN" in os.environ
    viewer = os.environ.get("AFTERFIX_VIEWER_TOKEN") if viewer_present else None
    if not _valid_token(operator):
        return None
    if viewer_present and not _valid_token(viewer):
        return None
    if viewer is not None and hmac.compare_digest(viewer, operator):
        return None
    return operator, viewer


def reset_allowed():
    import os

    return os.environ.get("AFTERFIX_AGENT_MODE") != "1" and os.environ.get("AFTERFIX_ALLOW_RESET") == "1"


class InvestigationGate:
    """Threading-safe, nonblocking: max 1 in flight, max 3 starts / 60s.

    No context-manager API: callers must pair a successful try_acquire()
    with release() themselves (e.g. try/finally), so a failed nested
    acquire can never release another caller's permit.
    """

    def __init__(self, clock=time.monotonic, max_starts=3, window_seconds=60.0):
        self._clock = clock
        self._max_starts = int(max_starts)
        self._window = float(window_seconds)
        self._lock = threading.Lock()
        self._in_flight = 0
        self._starts = deque()
        self._retry_after = 1

    def _prune(self, now):
        cutoff = now - self._window
        while self._starts and self._starts[0] <= cutoff:
            self._starts.popleft()

    def try_acquire(self):
        with self._lock:
            now = float(self._clock())
            self._prune(now)
            if self._in_flight >= 1:
                self._retry_after = 1
                return False
            if len(self._starts) >= self._max_starts:
                wait = self._starts[0] + self._window - now if self._starts else 1
                self._retry_after = max(1, int(math.ceil(wait)))
                return False
            self._starts.append(now)
            self._in_flight += 1
            self._retry_after = 0
            return True

    def retry_after(self):
        with self._lock:
            now = float(self._clock())
            self._prune(now)
            if self._in_flight >= 1:
                return 1
            if len(self._starts) >= self._max_starts and self._starts:
                return max(1, int(math.ceil(self._starts[0] + self._window - now)))
            return max(1, int(self._retry_after or 1))

    def release(self):
        with self._lock:
            if self._in_flight > 0:
                self._in_flight -= 1


async def _send_json(send, status, detail, extra_headers=None):
    import json

    body = json.dumps({"detail": detail}).encode("utf-8")
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode("ascii")),
        (b"cache-control", b"no-store"),
    ]
    if extra_headers:
        headers.extend(extra_headers)
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body, "more_body": False})


def _no_store_send(send):
    async def wrapped(message):
        if message.get("type") == "http.response.start":
            headers = [
                (k, v) for k, v in message.get("headers", []) if k.lower() != b"cache-control"
            ]
            headers.append((b"cache-control", b"no-store"))
            message = dict(message)
            message["headers"] = headers
        await send(message)

    return wrapped


class AuthMiddleware:
    """Pure-ASGI: authenticate/authorize first, then bound and replay body."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        scope_type = scope.get("type")
        if scope_type == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope_type == "websocket":
            await send({"type": "websocket.close", "code": 4401})
            return
        if scope_type != "http":
            await self.app(scope, receive, send)
            return

        method = str(scope.get("method", "")).upper()
        path = scope.get("path", "")
        if method == "GET" and path == "/health":
            await self.app(scope, receive, send)
            return

        send = _no_store_send(send)
        tokens = _load_tokens()
        if tokens is None:
            await _send_json(send, 503, "authentication unavailable")
            return
        operator, viewer = tokens

        auth_values = [v for k, v in scope.get("headers", []) if k.lower() == b"authorization"]
        www = [(b"www-authenticate", b"Bearer")]
        if not auth_values and public_read_allowed(method, path):
            actor = Actor(actor_id="viewer", role="viewer")
        else:
            if len(auth_values) != 1:
                await _send_json(send, 401, "unauthorized", www)
                return
            raw = auth_values[0]
            if len(raw) > 256:
                await _send_json(send, 401, "unauthorized", www)
                return
            try:
                text = raw.decode("ascii")
            except Exception:
                await _send_json(send, 401, "unauthorized", www)
                return
            parts = text.split()
            if len(parts) != 2 or parts[0].lower() != "bearer":
                await _send_json(send, 401, "unauthorized", www)
                return
            candidate = parts[1]
            if len(candidate) > 128 or not candidate.isascii():
                await _send_json(send, 401, "unauthorized", www)
                return
            cand = candidate.encode("ascii")
            is_operator = hmac.compare_digest(cand, operator.encode("ascii"))
            is_viewer = (
                hmac.compare_digest(cand, viewer.encode("ascii")) if viewer is not None else False
            )
            if is_operator:
                actor = Actor(actor_id="operator", role="operator")
            elif is_viewer:
                actor = Actor(actor_id="viewer", role="viewer")
            else:
                await _send_json(send, 401, "unauthorized", www)
                return

        if actor.role == "viewer" and not (method == "GET" or (method == "POST" and path == "/verify")):
            await _send_json(send, 403, "forbidden")
            return
        if method == "POST" and path == "/admin/reset" and not reset_allowed():
            await _send_json(send, 403, "reset disabled")
            return

        state = dict(scope.get("state") or {})
        state["actor"] = actor
        scope["state"] = state
        scope["afterfix_actor"] = actor

        # Bounded actual body read for every protected method (including
        # GET), based on streamed bytes rather than Content-Length alone.
        body = bytearray()
        deadline = asyncio.get_running_loop().time() + BODY_READ_TIMEOUT_SECONDS
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                await _send_json(send, 408, "request timeout")
                return
            try:
                message = await asyncio.wait_for(receive(), timeout=remaining)
            except asyncio.TimeoutError:
                await _send_json(send, 408, "request timeout")
                return
            if message.get("type") == "http.disconnect":
                return
            if message.get("type") != "http.request":
                continue
            chunk = message.get("body", b"") or b""
            if chunk:
                # Reject before copying the chunk into the buffer.
                if len(body) + len(chunk) > MAX_BODY_BYTES:
                    await _send_json(send, 413, "request body too large")
                    return
                body.extend(chunk)
            if not message.get("more_body", False):
                break

        replay_state = {"sent": False}
        body_bytes = bytes(body)

        async def replay_receive():
            if not replay_state["sent"]:
                replay_state["sent"] = True
                return {"type": "http.request", "body": body_bytes, "more_body": False}
            # Further reads belong to the real transport, not a synthetic
            # empty-body loop.
            return await receive()

        await self.app(scope, replay_receive, send)

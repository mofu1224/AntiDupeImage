from __future__ import annotations

import json
import secrets
from collections.abc import Awaitable, Callable
from urllib.parse import parse_qs

from fastapi import HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader, APIKeyQuery
from starlette.types import Message, Receive, Scope, Send

from app.core.config import get_settings


TOKEN_HEADER = "X-AntiDupeImage-Token"
TOKEN_QUERY = "token"

_header_scheme = APIKeyHeader(name=TOKEN_HEADER, auto_error=False)
_query_scheme = APIKeyQuery(name=TOKEN_QUERY, auto_error=False)

PUBLIC_API_PATHS = {"/api/docs", "/api/openapi.json"}
BODY_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class ApiBoundaryMiddleware:
    def __init__(self, app: Callable[..., Awaitable[None]], *, api_token: str, max_body_bytes: int):
        self.app = app
        self.api_token = api_token
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path", "")).rstrip("/") or "/"
        method = str(scope.get("method", "GET")).upper()
        if not path.startswith("/api") or path in PUBLIC_API_PATHS or method == "OPTIONS":
            await self.app(scope, receive, send)
            return

        if not self._has_valid_token(scope, path):
            await _send_json(
                send, status.HTTP_401_UNAUTHORIZED, "A valid local API token is required"
            )
            return

        if method not in BODY_METHODS:
            await self.app(scope, receive, send)
            return

        content_length = _content_length(scope)
        if content_length is not None and content_length > self.max_body_bytes:
            await _send_json(send, status.HTTP_413_CONTENT_TOO_LARGE, "Request body is too large")
            return
        replay_receive = await self._bounded_receive(receive, send)
        if replay_receive is None:
            return

        await self.app(scope, replay_receive, send)

    def _has_valid_token(self, scope: Scope, path: str) -> bool:
        headers = {
            key.lower(): value
            for key, value in scope.get("headers", [])
            if isinstance(key, bytes) and isinstance(value, bytes)
        }
        supplied = headers.get(TOKEN_HEADER.lower().encode("ascii"), b"").decode(
            "utf-8", errors="ignore"
        )
        if not supplied and _query_token_allowed_path(path):
            query = parse_qs(
                bytes(scope.get("query_string", b"")).decode("utf-8", errors="ignore"),
                keep_blank_values=True,
            )
            supplied = query.get(TOKEN_QUERY, [""])[0]
        return bool(supplied) and secrets.compare_digest(supplied, self.api_token)

    async def _bounded_receive(self, receive: Receive, send: Send) -> Receive | None:
        chunks: list[bytes] = []
        total = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return _replay_messages([message])
            if message["type"] != "http.request":
                continue
            chunk = bytes(message.get("body", b""))
            total += len(chunk)
            if total > self.max_body_bytes:
                await _send_json(
                    send,
                    status.HTTP_413_CONTENT_TOO_LARGE,
                    "Request body is too large",
                )
                return None
            chunks.append(chunk)
            if not message.get("more_body", False):
                return _replay_messages(
                    [{"type": "http.request", "body": b"".join(chunks), "more_body": False}]
                )


def require_api_token(
    request: Request,
    header_token: str | None = Security(_header_scheme),
    query_token: str | None = Security(_query_scheme),
) -> None:
    supplied = header_token or ""
    if not supplied and _query_token_allowed(request):
        supplied = query_token or ""
    expected = get_settings().api_token
    if not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A valid local API token is required",
        )


def _query_token_allowed(request: Request) -> bool:
    route = request.scope.get("route")
    route_path = getattr(route, "path", "")
    if route_path.endswith("/scans/{scan_id}/events"):
        return True
    request_path = request.url.path.rstrip("/")
    return _query_token_allowed_path(request_path)


def _query_token_allowed_path(path: str) -> bool:
    return "/scans/" in path and path.endswith("/events")


def _content_length(scope: Scope) -> int | None:
    for key, value in scope.get("headers", []):
        if key.lower() != b"content-length":
            continue
        try:
            parsed = int(value.decode("ascii"))
        except (UnicodeDecodeError, ValueError):
            return None
        return parsed if parsed >= 0 else None
    return None


def _replay_messages(messages: list[Message]) -> Receive:
    pending = list(messages)

    async def receive() -> Message:
        if pending:
            return pending.pop(0)
        return {"type": "http.request", "body": b"", "more_body": False}

    return receive


async def _send_json(send: Send, status_code: int, detail: str) -> None:
    body = json.dumps({"detail": detail}, separators=(",", ":")).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status_code,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})

"""Bounds on what unauthenticated clients can make the server write to disk.

OAuth dynamic client registration (`/register`) and authorization requests (`/authorize`) must
work before sign-in, and FastMCP persists both. Without limits a client could fill the disk
(measured: one 1 MB `client_name` became a 1.8 MB file). Defences here: a request body cap, global
hourly caps on those endpoints, and an OAuth store we own so expired entries are actually culled.
The host-level guarantee is a size-capped filesystem for the state directory (deploy/pi-setup.sh).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator
from pathlib import Path

from cryptography.fernet import Fernet
from fastmcp.server.auth.jwt_issuer import derive_jwt_key
from key_value.aio.protocols import AsyncKeyValue
from key_value.aio.stores.filetree import (
    FileTreeStore,
    FileTreeV1CollectionSanitizationStrategy,
    FileTreeV1KeySanitizationStrategy,
)
from key_value.aio.wrappers.encryption import FernetEncryptionWrapper
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)

MAX_BODY_BYTES = 64 * 1024
# Global (not per client: all Funnel traffic arrives from loopback) hourly caps. Claude uses
# CIMD and never calls /register; /authorize runs once per sign-in.
UNAUTHENTICATED_WRITE_LIMITS = {("POST", "/register"): 20, ("GET", "/authorize"): 60}
CULL_INTERVAL_SECONDS = 3600


class RequestGuard:
    """ASGI middleware: 413 for bodies over the cap, 429 above the hourly endpoint limits."""

    def __init__(
        self,
        app: ASGIApp,
        max_body_bytes: int = MAX_BODY_BYTES,
        limits: dict[tuple[str, str], int] | None = None,
    ) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes
        self.limits = UNAUTHENTICATED_WRITE_LIMITS if limits is None else limits
        # ponytail: fixed one-hour windows held in memory; reset on restart.
        self._windows: dict[tuple[str, str], tuple[int, int]] = {}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        key = (scope["method"], scope["path"].rstrip("/") or "/")
        if key in self.limits and not self._allow(key):
            await _plain(send, 429, "Too many requests", {"retry-after": "3600"})
            return
        declared = dict(scope.get("headers") or []).get(b"content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > self.max_body_bytes):
            await _plain(send, 413, "Request body too large")
            return
        if scope["method"] in ("POST", "PUT", "PATCH") and declared is None:
            body = await self._read_bounded(receive)
            if body is None:
                await _plain(send, 413, "Request body too large")
                return
            receive = _replay(body)
        await self.app(scope, receive, send)

    def _allow(self, key: tuple[str, str]) -> bool:
        window = int(time.time() // 3600)
        current, count = self._windows.get(key, (window, 0))
        if current != window:
            count = 0
        if count >= self.limits[key]:
            logger.warning("rate limit hit", extra={"fields": {"endpoint": " ".join(key)}})
            return False
        self._windows[key] = (window, count + 1)
        return True

    async def _read_bounded(self, receive: Receive) -> bytes | None:
        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                break
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > self.max_body_bytes:
                return None
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        return b"".join(chunks)


def _replay(body: bytes) -> Receive:
    sent = False

    async def receive() -> Message:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return receive


async def _plain(send: Send, status: int, text: str, headers: dict[str, str] | None = None) -> None:
    raw = [(b"content-type", b"text/plain; charset=utf-8")]
    raw += [(k.encode(), v.encode()) for k, v in (headers or {}).items()]
    await send({"type": "http.response.start", "status": status, "headers": raw})
    await send({"type": "http.response.body", "body": text.encode()})


def build_oauth_storage(home: Path, jwt_signing_key: str) -> tuple[AsyncKeyValue, FileTreeStore]:
    """Encrypted file store for OAuth state; the raw store is returned so it can be culled."""
    directory = home / "oauth-store"
    directory.mkdir(parents=True, exist_ok=True)
    store = FileTreeStore(
        data_directory=directory,
        key_sanitization_strategy=FileTreeV1KeySanitizationStrategy(directory),
        collection_sanitization_strategy=FileTreeV1CollectionSanitizationStrategy(directory),
    )
    key = derive_jwt_key(high_entropy_material=jwt_signing_key, salt="polish-legal-mcp-oauth-store")
    wrapped = FernetEncryptionWrapper(
        key_value=store, fernet=Fernet(key), raise_on_decryption_error=False
    )
    return wrapped, store


@contextlib.asynccontextmanager
async def periodic_cull(store: FileTreeStore | None) -> AsyncIterator[None]:
    """Delete expired OAuth entries hourly (FastMCP itself never culls its store)."""
    if store is None:
        yield
        return

    async def loop() -> None:
        while True:
            try:
                await store.cull()
            except Exception:
                logger.warning("oauth store cull failed", exc_info=True)
            await asyncio.sleep(CULL_INTERVAL_SECONDS)

    task = asyncio.create_task(loop())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

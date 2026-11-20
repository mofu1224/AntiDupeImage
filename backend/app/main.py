from __future__ import annotations

import asyncio
import os
import re
import secrets
import sqlite3
import stat
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.types import Scope

from app.api.routes import router
from app.core.config import get_settings
from app.core.db import get_storage
from app.core.security import ApiBoundaryMiddleware
from app.services.scanner import scan_manager


LAUNCH_HEALTH_PATH = "/_antidupeimage/launch-health"
LAUNCH_NONCE_HEADER = "X-AntiDupeImage-Launch-Nonce"
BOOTSTRAP_PATH = "/_antidupeimage/bootstrap"
BOOTSTRAP_HEADER = "X-AntiDupeImage-Bootstrap"
BOOTSTRAP_TTL_SECONDS = 60
BOOTSTRAP_META_PATTERN = re.compile(
    r'<meta name="antidupeimage-bootstrap" content="([A-Za-z0-9_-]{40,128})">'
)


class RevalidatingStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        storage = get_storage()
        storage.init_schema()
        scan_manager.recover_interrupted()
        yield
    finally:
        scan_manager.shutdown()


def create_app() -> FastAPI:
    settings = get_settings()
    get_storage().init_schema()
    cors_origins = list(settings.cors_origins)
    launcher_ui_origin = os.getenv("ANTIDUPEIMAGE_UI_ORIGIN", "").strip()
    launcher_nonce = os.getenv("ANTIDUPEIMAGE_LAUNCH_NONCE", "").strip()
    handoff_path = os.getenv("ANTIDUPEIMAGE_HANDOFF_PATH", "").strip()
    bootstrap_token = _read_bootstrap_handoff_token(handoff_path)
    bootstrap_lock = asyncio.Lock()
    bootstrap_consumed = False
    bootstrap_expires_at = time.monotonic() + BOOTSTRAP_TTL_SECONDS
    if launcher_ui_origin and launcher_ui_origin not in cors_origins:
        cors_origins.append(launcher_ui_origin)

    app = FastAPI(
        title="AntiDupeImage API",
        version="0.2.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.add_exception_handler(sqlite3.OperationalError, sqlite_operational_error)  # type: ignore[arg-type]
    app.add_exception_handler(sqlite3.DatabaseError, sqlite_database_error)  # type: ignore[arg-type]
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(
        ApiBoundaryMiddleware,
        api_token=settings.api_token,
        max_body_bytes=settings.max_request_body_bytes,
    )

    @app.get(LAUNCH_HEALTH_PATH, include_in_schema=False)
    async def launch_health(request: Request) -> JSONResponse:
        expected = os.getenv("ANTIDUPEIMAGE_LAUNCH_NONCE", "")
        supplied = request.headers.get(LAUNCH_NONCE_HEADER, "")
        headers = {"Cache-Control": "no-store"}
        if not expected or not supplied or not secrets.compare_digest(supplied, expected):
            return JSONResponse(status_code=404, content={"detail": "Not Found"}, headers=headers)
        return JSONResponse(content={"nonce": expected}, headers=headers)

    @app.post(BOOTSTRAP_PATH, include_in_schema=False)
    async def bootstrap(request: Request) -> JSONResponse:
        nonlocal bootstrap_consumed
        supplied = request.headers.get(BOOTSTRAP_HEADER, "")
        supplied_origin = request.headers.get("Origin", "")
        headers = {"Cache-Control": "no-store"}
        if (
            not bootstrap_token
            or not launcher_nonce
            or not launcher_ui_origin
            or time.monotonic() > bootstrap_expires_at
            or not supplied
            or not secrets.compare_digest(supplied, bootstrap_token)
            or not secrets.compare_digest(supplied_origin, launcher_ui_origin)
        ):
            return JSONResponse(status_code=404, content={"detail": "Not Found"}, headers=headers)
        async with bootstrap_lock:
            if bootstrap_consumed:
                return JSONResponse(
                    status_code=404,
                    content={"detail": "Not Found"},
                    headers=headers,
                )
            if not _remove_bootstrap_handoff(handoff_path):
                return JSONResponse(
                    status_code=404,
                    content={"detail": "Not Found"},
                    headers=headers,
                )
            bootstrap_consumed = True
        return JSONResponse(content={"api_token": settings.api_token}, headers=headers)

    app.include_router(router, prefix="/api")
    static_dir = os.getenv("ANTIDUPEIMAGE_STATIC_DIR", "").strip()
    if static_dir:
        app.mount("/", RevalidatingStaticFiles(directory=static_dir, html=True), name="portable-ui")
    return app


def _remove_bootstrap_handoff(raw_path: str) -> bool:
    validated = _validated_bootstrap_handoff(raw_path)
    if validated is None:
        return False
    path, directory = validated
    try:
        path.unlink()
        try:
            directory.rmdir()
        except OSError:
            pass
        return True
    except (FileNotFoundError, OSError, RuntimeError):
        return False


def _read_bootstrap_handoff_token(raw_path: str) -> str:
    validated = _validated_bootstrap_handoff(raw_path)
    if validated is None:
        return ""
    path, _ = validated
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ""
    if len(content) > 4096:
        return ""
    match = BOOTSTRAP_META_PATTERN.search(content)
    return match.group(1) if match else ""


def _validated_bootstrap_handoff(raw_path: str) -> tuple[Path, Path] | None:
    if not raw_path:
        return None
    try:
        path = Path(raw_path)
        directory = path.parent
        temp_root = Path(tempfile.gettempdir()).resolve(strict=True)
        resolved_directory = directory.resolve(strict=True)
        if (
            path.name != "handoff.html"
            or not resolved_directory.name.startswith("antidupeimage-handoff.")
            or not resolved_directory.is_relative_to(temp_root)
        ):
            return None
        directory_stat = directory.lstat()
        file_stat = path.lstat()
        if stat.S_ISLNK(directory_stat.st_mode) or not stat.S_ISDIR(directory_stat.st_mode):
            return None
        if stat.S_ISLNK(file_stat.st_mode) or not stat.S_ISREG(file_stat.st_mode):
            return None
        if os.name != "nt":
            current_uid = os.geteuid()
            if directory_stat.st_uid != current_uid or file_stat.st_uid != current_uid:
                return None
            if stat.S_IMODE(directory_stat.st_mode) & 0o077:
                return None
            if stat.S_IMODE(file_stat.st_mode) & 0o077:
                return None
        return path, directory
    except (FileNotFoundError, OSError, RuntimeError):
        return None


async def sqlite_operational_error(_: Request, exc: sqlite3.OperationalError) -> JSONResponse:
    message = str(exc).lower()
    if "locked" in message or "busy" in message:
        detail = "Local database is busy. Retry shortly."
        status_code = 503
    else:
        detail = "Local database operation failed."
        status_code = 500
    return JSONResponse(status_code=status_code, content={"detail": detail})


async def sqlite_database_error(_: Request, _exc: sqlite3.DatabaseError) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={"detail": "Local database operation failed."},
    )


app = create_app()

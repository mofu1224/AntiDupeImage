from __future__ import annotations

import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path


APP_NAME = "AntiDupeImage"
TOKEN_FILE_NAME = "api-token"


def _default_app_dir() -> Path:
    override = _path_env("ANTIDUPEIMAGE_HOME")
    if override:
        return Path(override).expanduser().resolve()

    location = Path(__file__).resolve()
    for parent in location.parents:
        if (parent / "scripts/portable-runtime.json").is_file():
            return parent / "data"
    return location.parents[3] / "data"


def _path_env(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _database_path(app_dir: Path) -> Path:
    override = _path_env("ANTIDUPEIMAGE_DB")
    if override:
        return Path(override).expanduser().resolve()
    return app_dir / "antidupeimage.sqlite3"


def _load_or_create_api_token(app_dir: Path) -> str:
    override = os.getenv("ANTIDUPEIMAGE_API_TOKEN")
    if override and override.strip():
        return override.strip()

    token_path = app_dir / TOKEN_FILE_NAME
    existing = _read_existing_api_token(token_path, retries=50)
    if existing:
        return existing
    if existing == "":
        raise RuntimeError(f"API token file is empty: {token_path}")

    token = secrets.token_urlsafe(32)
    try:
        descriptor = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        existing = _read_existing_api_token(token_path, retries=50)
        if existing:
            return existing
        raise RuntimeError(f"API token file is empty: {token_path}")
    with os.fdopen(descriptor, "w", encoding="utf-8") as token_file:
        token_file.write(f"{token}\n")
    _restrict_token_permissions(token_path)
    return token


def _read_existing_api_token(token_path: Path, *, retries: int) -> str | None:
    for attempt in range(retries + 1):
        try:
            token = token_path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return None
        if token:
            _restrict_token_permissions(token_path)
            return token
        if attempt < retries:
            time.sleep(0.01)
    return ""


def _restrict_token_permissions(token_path: Path) -> None:
    try:
        token_path.chmod(0o600)
    except OSError:
        pass


def _read_int_env(name: str, default: int, *, minimum: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw.strip())
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer greater than or equal to {minimum}") from exc
    if value < minimum:
        raise ValueError(f"{name} must be an integer greater than or equal to {minimum}")
    return value


def _default_feature_workers() -> int:
    cpu_count = os.cpu_count() or 1
    return min(4, max(1, (cpu_count + 1) // 2))


@dataclass(frozen=True)
class Settings:
    app_dir: Path
    db_path: Path
    thumbnail_size: int
    cors_origins: list[str]
    default_clip_model: str
    default_clip_pretrained: str
    api_token: str
    max_scan_page_size: int
    max_group_page_size: int
    max_request_body_bytes: int
    max_animation_frames: int
    max_decoded_pixels: int
    max_candidate_pairs: int
    max_candidate_comparisons: int
    feature_workers: int


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is not None:
        return _settings

    app_dir = _default_app_dir()
    app_dir.mkdir(parents=True, exist_ok=True)
    _settings = Settings(
        app_dir=app_dir,
        db_path=_database_path(app_dir),
        thumbnail_size=_read_int_env("ANTIDUPEIMAGE_THUMBNAIL_SIZE", 384, minimum=64),
        cors_origins=[
            "http://127.0.0.1:5173",
            "http://localhost:5173",
            "http://127.0.0.1:4173",
            "http://localhost:4173",
        ],
        default_clip_model=os.getenv("ANTIDUPEIMAGE_CLIP_MODEL", "ViT-B-32"),
        default_clip_pretrained=os.getenv(
            "ANTIDUPEIMAGE_CLIP_PRETRAINED", "laion2b_s34b_b79k"
        ),
        api_token=_load_or_create_api_token(app_dir),
        max_scan_page_size=_read_int_env("ANTIDUPEIMAGE_MAX_SCAN_PAGE_SIZE", 200, minimum=1),
        max_group_page_size=_read_int_env("ANTIDUPEIMAGE_MAX_GROUP_PAGE_SIZE", 500, minimum=1),
        max_request_body_bytes=_read_int_env(
            "ANTIDUPEIMAGE_MAX_REQUEST_BODY_BYTES",
            1_048_576,
            minimum=1,
        ),
        max_animation_frames=_read_int_env(
            "ANTIDUPEIMAGE_MAX_ANIMATION_FRAMES",
            1_000,
            minimum=1,
        ),
        max_decoded_pixels=_read_int_env(
            "ANTIDUPEIMAGE_MAX_DECODED_PIXELS",
            50_000_000,
            minimum=1,
        ),
        max_candidate_pairs=_read_int_env(
            "ANTIDUPEIMAGE_MAX_CANDIDATE_PAIRS",
            100_000,
            minimum=1,
        ),
        max_candidate_comparisons=_read_int_env(
            "ANTIDUPEIMAGE_MAX_CANDIDATE_COMPARISONS",
            1_000_000,
            minimum=1,
        ),
        feature_workers=_read_int_env(
            "ANTIDUPEIMAGE_FEATURE_WORKERS",
            _default_feature_workers(),
            minimum=1,
        ),
    )
    return _settings


def reset_settings() -> None:
    global _settings
    _settings = None

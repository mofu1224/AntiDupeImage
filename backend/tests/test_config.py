from __future__ import annotations

# pyright: reportPrivateUsage=false

import os
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.core.config import _load_or_create_api_token, get_settings, reset_settings


def test_api_token_is_created_with_restricted_permissions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTIDUPEIMAGE_API_TOKEN", raising=False)

    token = _load_or_create_api_token(tmp_path)
    token_path = tmp_path / "api-token"

    assert token
    assert token_path.read_text(encoding="utf-8").strip() == token
    if os.name != "nt":
        assert stat.S_IMODE(token_path.stat().st_mode) == 0o600


def test_empty_api_token_override_does_not_disable_authentication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTIDUPEIMAGE_API_TOKEN", "   ")

    assert _load_or_create_api_token(tmp_path)


def test_empty_api_token_file_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTIDUPEIMAGE_API_TOKEN", raising=False)
    (tmp_path / "api-token").write_text("", encoding="utf-8")

    with pytest.raises(RuntimeError, match="token file is empty"):
        _load_or_create_api_token(tmp_path)


def test_concurrent_api_token_creation_returns_one_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTIDUPEIMAGE_API_TOKEN", raising=False)
    barrier = threading.Barrier(8)

    def load_token(_: int) -> str:
        barrier.wait()
        return _load_or_create_api_token(tmp_path)

    with ThreadPoolExecutor(max_workers=8) as executor:
        tokens = list(executor.map(load_token, range(8)))

    assert len(set(tokens)) == 1


def test_invalid_integer_environment_value_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTIDUPEIMAGE_HOME", str(tmp_path))
    monkeypatch.setenv("ANTIDUPEIMAGE_THUMBNAIL_SIZE", "not-an-int")
    reset_settings()

    with pytest.raises(ValueError, match="ANTIDUPEIMAGE_THUMBNAIL_SIZE"):
        get_settings()

    reset_settings()


def test_too_small_integer_environment_value_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTIDUPEIMAGE_HOME", str(tmp_path))
    monkeypatch.setenv("ANTIDUPEIMAGE_MAX_SCAN_PAGE_SIZE", "0")
    reset_settings()

    with pytest.raises(ValueError, match="ANTIDUPEIMAGE_MAX_SCAN_PAGE_SIZE"):
        get_settings()

    reset_settings()


def test_feature_workers_can_be_configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTIDUPEIMAGE_HOME", str(tmp_path))
    monkeypatch.setenv("ANTIDUPEIMAGE_FEATURE_WORKERS", "2")
    reset_settings()

    assert get_settings().feature_workers == 2

    reset_settings()


def test_path_environment_values_are_normalized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_dir = tmp_path / "app"
    db_path = tmp_path / "db.sqlite3"
    monkeypatch.setenv("ANTIDUPEIMAGE_HOME", f" {app_dir} ")
    monkeypatch.setenv("ANTIDUPEIMAGE_DB", f" {db_path} ")
    reset_settings()

    settings = get_settings()

    assert settings.app_dir == app_dir.resolve()
    assert settings.db_path == db_path.resolve()

    reset_settings()


def test_blank_database_path_falls_back_to_app_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTIDUPEIMAGE_HOME", str(tmp_path))
    monkeypatch.setenv("ANTIDUPEIMAGE_DB", "   ")
    reset_settings()

    assert get_settings().db_path == tmp_path / "antidupeimage.sqlite3"

    reset_settings()


def test_blank_app_home_uses_repository_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTIDUPEIMAGE_HOME", "   ")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setattr("platform.system", lambda: "Linux")
    # Read only: checking the default must not create real repository data.
    from app.core.config import _default_app_dir

    assert _default_app_dir() == Path(__file__).resolve().parents[2] / "data"


def test_installed_portable_package_finds_repository_data(tmp_path, monkeypatch):
    from app.core.config import _default_app_dir

    monkeypatch.delenv("ANTIDUPEIMAGE_HOME", raising=False)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/portable-runtime.json").write_text("{}")
    installed = tmp_path / ".portable/venv/Lib/site-packages/app/core/config.py"
    monkeypatch.setattr("app.core.config.__file__", str(installed))
    assert _default_app_dir() == tmp_path / "data"

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from app import main


def request(app: FastAPI, path: str, *, method: str = "GET", **kwargs: Any) -> httpx.Response:
    async def perform() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(perform())


class TestStorage:
    def init_schema(self) -> None:
        return None


def test_portable_static_ui_preserves_api_auth_and_host_boundary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(main, "get_storage", lambda: TestStorage())
    monkeypatch.setenv("ANTIDUPEIMAGE_STATIC_DIR", str(tmp_path))
    (tmp_path / "index.html").write_text("<html>Portable UI</html>", encoding="utf-8")
    app = main.create_app()
    assert request(app, "/").text == "<html>Portable UI</html>"
    assert request(app, "/api/system").status_code == 401
    assert request(app, "/", headers={"Host": "untrusted.example"}).status_code == 400
    assert request(app, "/%2e%2e/private.txt").status_code == 404


def test_portable_html_is_revalidated_after_a_ui_update(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(main, "get_storage", lambda: TestStorage())
    monkeypatch.setenv("ANTIDUPEIMAGE_STATIC_DIR", str(tmp_path))
    index = tmp_path / "index.html"
    index.write_text("<html>Previous UI</html>", encoding="utf-8")
    app = main.create_app()
    previous = request(app, "/")
    assert previous.headers.get("cache-control") == "no-cache"
    index.write_text("<html>Rebuilt OpenCode-inspired UI</html>", encoding="utf-8")
    current = request(app, "/", headers={"If-None-Match": previous.headers["etag"]})
    assert current.status_code == 200
    assert current.text == "<html>Rebuilt OpenCode-inspired UI</html>"
    assert current.headers.get("cache-control") == "no-cache"


def test_lifespan_shuts_down_scan_manager_when_serving_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shutdown_calls: list[bool] = []

    monkeypatch.setattr(main, "get_storage", lambda: TestStorage())
    monkeypatch.setattr(main.scan_manager, "recover_interrupted", lambda: 0)
    monkeypatch.setattr(main.scan_manager, "shutdown", lambda: shutdown_calls.append(True))

    async def exercise_lifespan() -> None:
        with pytest.raises(RuntimeError, match="serving failed"):
            async with main.lifespan(None):  # type: ignore[arg-type]
                raise RuntimeError("serving failed")

    asyncio.run(exercise_lifespan())

    assert shutdown_calls == [True]


def test_launch_health_requires_the_exact_nonce(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "get_storage", lambda: TestStorage())
    monkeypatch.setenv("ANTIDUPEIMAGE_LAUNCH_NONCE", "expected-launch-nonce")
    app = main.create_app()

    for headers in ({}, {main.LAUNCH_NONCE_HEADER: "wrong-launch-nonce"}):
        response = request(app, main.LAUNCH_HEALTH_PATH, headers=headers)
        assert response.status_code == 404
        assert response.headers["cache-control"] == "no-store"

    response = request(
        app,
        main.LAUNCH_HEALTH_PATH,
        headers={main.LAUNCH_NONCE_HEADER: "expected-launch-nonce"},
    )
    assert response.status_code == 200
    assert response.json() == {"nonce": "expected-launch-nonce"}
    assert response.headers["cache-control"] == "no-store"


def test_launch_health_is_unavailable_without_a_configured_nonce(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main, "get_storage", lambda: TestStorage())
    monkeypatch.delenv("ANTIDUPEIMAGE_LAUNCH_NONCE", raising=False)
    app = main.create_app()

    response = request(
        app,
        main.LAUNCH_HEALTH_PATH,
        headers={main.LAUNCH_NONCE_HEADER: "any-launch-nonce"},
    )

    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"


def test_launcher_ui_origin_is_allowed_for_random_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main, "get_storage", lambda: TestStorage())
    monkeypatch.setenv("ANTIDUPEIMAGE_UI_ORIGIN", "http://127.0.0.1:54321")
    app = main.create_app()

    response = request(
        app,
        "/api/system",
        method="OPTIONS",
        headers={
            "Origin": "http://127.0.0.1:54321",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:54321"


def test_bootstrap_is_origin_bound_single_use_and_no_store(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    handoff_dir = tmp_path / "antidupeimage-handoff.test"
    handoff_dir.mkdir(mode=0o700)
    handoff_path = handoff_dir / "handoff.html"
    handoff_path.write_text(
        '<meta name="antidupeimage-bootstrap" content="one-time-bootstrap-capability-1234567890">',
        encoding="utf-8",
    )
    handoff_path.chmod(0o600)
    monkeypatch.setattr(main, "get_storage", lambda: TestStorage())
    monkeypatch.setenv("ANTIDUPEIMAGE_UI_ORIGIN", "http://127.0.0.1:54321")
    monkeypatch.setenv("ANTIDUPEIMAGE_LAUNCH_NONCE", "launcher-owner")
    monkeypatch.setenv("ANTIDUPEIMAGE_HANDOFF_PATH", str(handoff_path))
    app = main.create_app()
    headers = {
        "Origin": "http://127.0.0.1:54321",
        main.BOOTSTRAP_HEADER: "one-time-bootstrap-capability-1234567890",
    }

    wrong = request(
        app,
        main.BOOTSTRAP_PATH,
        method="POST",
        headers={**headers, main.BOOTSTRAP_HEADER: "wrong-bootstrap-capability-1234567890"},
    )
    assert wrong.status_code == 404
    assert wrong.headers["cache-control"] == "no-store"
    assert handoff_path.exists()

    wrong_origin = request(
        app,
        main.BOOTSTRAP_PATH,
        method="POST",
        headers={**headers, "Origin": "http://127.0.0.1:54322"},
    )
    assert wrong_origin.status_code == 404

    success = request(app, main.BOOTSTRAP_PATH, method="POST", headers=headers)
    assert success.status_code == 200
    assert success.json() == {"api_token": main.get_settings().api_token}
    assert success.headers["cache-control"] == "no-store"
    assert not handoff_path.exists()

    reused = request(app, main.BOOTSTRAP_PATH, method="POST", headers=headers)
    assert reused.status_code == 404
    assert reused.headers["cache-control"] == "no-store"


def test_bootstrap_does_not_unlink_an_unvalidated_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    victim_dir = tmp_path / "unrelated-private-directory"
    victim_dir.mkdir(mode=0o700)
    victim = victim_dir / "handoff.html"
    victim.write_text("must remain", encoding="utf-8")
    victim.chmod(0o600)
    monkeypatch.setattr(main, "get_storage", lambda: TestStorage())
    monkeypatch.setenv("ANTIDUPEIMAGE_UI_ORIGIN", "http://127.0.0.1:54321")
    monkeypatch.setenv("ANTIDUPEIMAGE_LAUNCH_NONCE", "launcher-owner")
    monkeypatch.setenv("ANTIDUPEIMAGE_HANDOFF_PATH", str(victim))
    app = main.create_app()

    response = request(
        app,
        main.BOOTSTRAP_PATH,
        method="POST",
        headers={
            "Origin": "http://127.0.0.1:54321",
            main.BOOTSTRAP_HEADER: "one-time-bootstrap-capability-1234567890",
        },
    )

    assert response.status_code == 404
    assert victim.read_text(encoding="utf-8") == "must remain"

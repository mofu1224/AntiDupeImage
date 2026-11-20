from __future__ import annotations

import asyncio
from dataclasses import replace
import os
import sqlite3
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from starlette.types import Message

from app.api import routes
from app.core.db import Storage
from app.core.config import get_settings
from app.main import create_app
from app.services import operations
from app.services.features import extract_image_features
from app.services.scanner import scan_manager
from app.services.file_actions import SnapshotFileStatus

Image = pytest.importorskip("PIL.Image")


def make_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[FastAPI, Storage]:
    storage = Storage(tmp_path / "api.sqlite3")
    storage.init_schema()
    monkeypatch.setattr(routes, "get_storage", lambda: storage)
    monkeypatch.setattr(operations, "get_storage", lambda: storage)
    monkeypatch.setattr("app.main.get_storage", lambda: storage)
    scan_manager.storage = storage
    return create_app(), storage


def request(app: FastAPI, method: str, path: str, **kwargs: Any) -> httpx.Response:
    async def perform() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(perform())


def require_row(row: dict[str, Any] | None) -> dict[str, Any]:
    assert row is not None
    return row


def create_exact_group(storage: Storage, image_dir: Path) -> tuple[int, int, list[int]]:
    first_path = image_dir / "first.png"
    second_path = image_dir / "second.png"
    Image.new("RGB", (20, 20), (10, 20, 30)).save(first_path)
    Image.new("RGB", (20, 20), (10, 20, 30)).save(second_path)
    scan_id = storage.create_scan(str(image_dir), True, "model", "pretrained", "cpu", {})
    scan_images: list[int] = []
    for path in (first_path, second_path):
        feature = extract_image_features(path)
        cache_id = storage.upsert_image(feature)
        scan_images.append(storage.create_scan_image(scan_id, cache_id, feature))
    storage.create_groups(
        scan_id,
        [
            {
                "score": 1.0,
                "reason": "exact-pixel",
                "recommended_keep_id": scan_images[0],
                "members": [
                    {
                        "image_id": scan_images[0],
                        "score": 1.0,
                        "reason": "recommended-keep",
                    },
                    {"image_id": scan_images[1], "score": 1.0, "reason": "exact-pixel"},
                ],
            }
        ],
    )
    storage.update_scan(
        scan_id,
        status="completed",
        phase="completed",
        progress_detail="Completed with 1 duplicate group(s)",
        progress=1.0,
        duplicate_groups=1,
    )
    group_id = int(storage.get_groups(scan_id, 10, 0)[0]["id"])
    return scan_id, group_id, scan_images


def test_api_requires_local_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app, _ = make_app(tmp_path, monkeypatch)

    assert request(app, "GET", "/api/system").status_code == 401
    assert request(app, "GET", "/api/system?token=test-token").status_code == 401
    assert (
        request(
            app,
            "GET",
            "/api/system",
            headers={"X-AntiDupeImage-Token": "test-token"},
        ).status_code
        == 200
    )


def test_api_rejects_unauthenticated_body_before_consuming_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, _ = make_app(tmp_path, monkeypatch)
    consumed = 0
    messages: list[Message] = [
        {"type": "http.request", "body": b'{"confirm":', "more_body": True},
        {"type": "http.request", "body": b"true}", "more_body": False},
    ]
    sent: list[Message] = []

    async def receive() -> Message:
        nonlocal consumed
        consumed += 1
        return messages.pop(0)

    async def send(message: Message) -> None:
        sent.append(message)

    asyncio.run(
        app(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": "/api/system/reset",
                "raw_path": b"/api/system/reset",
                "query_string": b"",
                "headers": [(b"host", b"localhost")],
                "client": ("127.0.0.1", 12345),
                "server": ("localhost", 80),
            },
            receive,
            send,
        )
    )

    assert consumed == 0
    assert sent[0]["status"] == 401


def test_scan_history_can_be_filtered_by_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, storage = make_app(tmp_path, monkeypatch)
    completed_id = storage.create_scan(str(tmp_path), True, "model", "pretrained", "cpu", {})
    failed_id = storage.create_scan(str(tmp_path), True, "model", "pretrained", "cpu", {})
    cancelled_id = storage.create_scan(str(tmp_path), True, "model", "pretrained", "cpu", {})
    storage.update_scan(completed_id, status="completed", phase="completed")
    storage.update_scan(failed_id, status="failed", phase="failed")
    storage.update_scan(cancelled_id, status="cancelled", phase="cancelled")
    headers = {"X-AntiDupeImage-Token": "test-token"}

    all_response = request(app, "GET", "/api/scans?limit=2&offset=0", headers=headers)
    completed_response = request(
        app,
        "GET",
        "/api/scans?limit=50&offset=0&status=completed",
        headers=headers,
    )
    failed_response = request(
        app,
        "GET",
        "/api/scans?limit=50&offset=0&status=failed",
        headers=headers,
    )

    assert all_response.status_code == 200
    assert all_response.json()["total"] == 3
    assert len(all_response.json()["scans"]) == 2
    assert completed_response.status_code == 200
    assert completed_response.json()["total"] == 1
    assert [scan["id"] for scan in completed_response.json()["scans"]] == [completed_id]
    assert failed_response.status_code == 200
    assert failed_response.json()["total"] == 1
    assert [scan["id"] for scan in failed_response.json()["scans"]] == [failed_id]


def test_scan_history_rejects_unknown_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, _ = make_app(tmp_path, monkeypatch)

    response = request(
        app,
        "GET",
        "/api/scans?status=unknown",
        headers={"X-AntiDupeImage-Token": "test-token"},
    )

    assert response.status_code == 422


def test_api_rejects_oversized_chunked_body_before_route_parsing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = replace(get_settings(), max_request_body_bytes=8)
    monkeypatch.setattr("app.main.get_settings", lambda: settings)
    app, _ = make_app(tmp_path, monkeypatch)
    consumed = 0
    messages: list[Message] = [
        {"type": "http.request", "body": b'{"confir', "more_body": True},
        {"type": "http.request", "body": b'm":true}', "more_body": True},
        {"type": "http.request", "body": b"ignored", "more_body": False},
    ]
    sent: list[Message] = []

    async def receive() -> Message:
        nonlocal consumed
        consumed += 1
        return messages.pop(0)

    async def send(message: Message) -> None:
        sent.append(message)

    asyncio.run(
        app(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": "/api/system/reset",
                "raw_path": b"/api/system/reset",
                "query_string": b"",
                "headers": [
                    (b"host", b"localhost"),
                    (b"x-antidupeimage-token", b"test-token"),
                ],
                "client": ("127.0.0.1", 12345),
                "server": ("localhost", 80),
            },
            receive,
            send,
        )
    )

    assert consumed == 2
    assert sent[0]["status"] == 413


def test_api_rejects_oversized_content_length_without_reading_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = replace(get_settings(), max_request_body_bytes=8)
    monkeypatch.setattr("app.main.get_settings", lambda: settings)
    app, _ = make_app(tmp_path, monkeypatch)
    consumed = 0

    async def receive() -> Message:
        nonlocal consumed
        consumed += 1
        return {"type": "http.request", "body": b"too large", "more_body": False}

    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    asyncio.run(
        app(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": "/api/system/reset",
                "raw_path": b"/api/system/reset",
                "query_string": b"",
                "headers": [
                    (b"host", b"localhost"),
                    (b"x-antidupeimage-token", b"test-token"),
                    (b"content-length", b"9"),
                ],
                "client": ("127.0.0.1", 12345),
                "server": ("localhost", 80),
            },
            receive,
            send,
        )
    )

    assert consumed == 0
    assert sent[0]["status"] == 413


def test_query_token_is_limited_to_scan_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, storage = make_app(tmp_path, monkeypatch)
    scan_id = storage.create_scan(str(tmp_path), True, "model", "pretrained", "cpu", {})
    storage.update_scan(
        scan_id,
        status="completed",
        phase="completed",
        progress_detail="Completed with 0 duplicate group(s)",
        progress=1.0,
    )

    unauthorized = request(app, "GET", f"/api/scans/{scan_id}/groups?token=test-token")
    event_response = request(app, "GET", f"/api/scans/{scan_id}/events?token=test-token")

    assert unauthorized.status_code == 401
    assert event_response.status_code == 200


def test_reset_app_data_requires_explicit_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, _ = make_app(tmp_path, monkeypatch)

    response = request(
        app,
        "POST",
        "/api/system/reset",
        headers={"X-AntiDupeImage-Token": "test-token"},
        json={"confirm": False},
    )

    assert response.status_code == 400


def test_reset_app_data_clears_history_and_cache_without_touching_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, storage = make_app(tmp_path, monkeypatch)
    routes.clear_thumbnail_cache()
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    first_path = image_dir / "first.png"
    second_path = image_dir / "second.png"
    Image.new("RGB", (20, 20), (10, 20, 30)).save(first_path)
    Image.new("RGB", (20, 20), (10, 20, 30)).save(second_path)
    scan_id = storage.create_scan(str(image_dir), True, "model", "pretrained", "cpu", {})
    scan_images: list[int] = []
    for path in (first_path, second_path):
        feature = extract_image_features(path)
        cache_id = storage.upsert_image(feature)
        scan_images.append(storage.create_scan_image(scan_id, cache_id, feature))
    storage.create_groups(
        scan_id,
        [
            {
                "score": 1.0,
                "reason": "exact-pixel",
                "recommended_keep_id": scan_images[0],
                "members": [
                    {
                        "image_id": scan_images[0],
                        "score": 1.0,
                        "reason": "recommended-keep",
                    },
                    {"image_id": scan_images[1], "score": 1.0, "reason": "exact-pixel"},
                ],
            }
        ],
    )
    storage.record_failure(scan_id, str(image_dir / "bad.txt"), "discovery", "bad file")
    storage.update_scan(
        scan_id,
        status="completed",
        phase="completed",
        progress_detail="Completed with 1 duplicate group(s)",
        progress=1.0,
        duplicate_groups=1,
    )
    headers = {"X-AntiDupeImage-Token": "test-token"}
    thumbnail_response = request(
        app,
        "GET",
        f"/api/images/{scan_images[0]}/thumbnail",
        headers=headers,
    )

    assert thumbnail_response.status_code == 200
    assert routes.get_thumbnail_cache_size() > 0

    response = request(
        app,
        "POST",
        "/api/system/reset",
        headers=headers,
        json={"confirm": True},
    )

    assert response.status_code == 200
    assert response.json() == {
        "scans_deleted": 1,
        "scan_images_deleted": 2,
        "groups_deleted": 1,
        "group_members_deleted": 2,
        "failures_deleted": 1,
        "cached_images_deleted": 2,
    }
    assert first_path.is_file()
    assert second_path.is_file()
    rows, total = storage.list_scans(50, 0)
    assert rows == []
    assert total == 0
    with storage.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM images").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM scan_images").fetchone()[0] == 0
    assert routes.get_thumbnail_cache_size() == 0


def test_reset_app_data_rejects_running_scans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, storage = make_app(tmp_path, monkeypatch)
    scan_id = storage.create_scan(str(tmp_path), True, "model", "pretrained", "cpu", {})

    response = request(
        app,
        "POST",
        "/api/system/reset",
        headers={"X-AntiDupeImage-Token": "test-token"},
        json={"confirm": True},
    )

    assert response.status_code == 409
    assert storage.get_scan(scan_id) is not None


def test_api_rejects_untrusted_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app, _ = make_app(tmp_path, monkeypatch)

    response = request(
        app,
        "GET",
        "/api/system",
        headers={"Host": "example.invalid", "X-AntiDupeImage-Token": "test-token"},
    )

    assert response.status_code == 400


def test_blank_scan_path_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app, _ = make_app(tmp_path, monkeypatch)

    response = request(
        app,
        "POST",
        "/api/scans",
        headers={"X-AntiDupeImage-Token": "test-token"},
        json={"path": "   "},
    )

    assert response.status_code == 422


def test_folder_dialog_returns_selected_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, _ = make_app(tmp_path, monkeypatch)
    selected = tmp_path / "images"
    selected.mkdir()

    def select_folder(initial_path: str | None = None) -> str:
        assert initial_path == str(tmp_path)
        return str(selected)

    monkeypatch.setattr(operations, "select_scan_folder", select_folder)

    response = request(
        app,
        "POST",
        "/api/dialogs/folder",
        headers={"X-AntiDupeImage-Token": "test-token"},
        json={"initial_path": str(tmp_path)},
    )

    assert response.status_code == 200
    assert response.json() == {"path": str(selected), "cancelled": False}


def test_folder_dialog_cancellation_and_unavailable_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, _ = make_app(tmp_path, monkeypatch)
    headers = {"X-AntiDupeImage-Token": "test-token"}

    def cancel_folder(_initial_path: str | None = None) -> str | None:
        return None

    monkeypatch.setattr(operations, "select_scan_folder", cancel_folder)

    cancelled = request(app, "POST", "/api/dialogs/folder", headers=headers, json={})

    def unavailable(_initial_path: str | None = None) -> str:
        raise operations.FolderDialogUnavailable("folder picker unavailable")

    monkeypatch.setattr(operations, "select_scan_folder", unavailable)
    unavailable_response = request(app, "POST", "/api/dialogs/folder", headers=headers, json={})

    assert cancelled.status_code == 200
    assert cancelled.json() == {"path": None, "cancelled": True}
    assert unavailable_response.status_code == 503


def test_missing_scan_event_stream_is_rejected_before_streaming(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, _ = make_app(tmp_path, monkeypatch)

    response = request(
        app,
        "GET",
        "/api/scans/999/events",
        headers={"X-AntiDupeImage-Token": "test-token"},
    )

    assert response.status_code == 404


def test_group_summaries_do_not_revalidate_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, storage = make_app(tmp_path, monkeypatch)
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    scan_id, _, scan_images = create_exact_group(storage, image_dir)
    storage.mark_moved([scan_images[1]])

    def fail_snapshot_status(*_args: Any, **_kwargs: Any) -> SnapshotFileStatus:
        raise AssertionError("group summaries must not inspect live file status")

    monkeypatch.setattr(operations, "snapshot_file_status", fail_snapshot_status)

    response = request(
        app,
        "GET",
        f"/api/scans/{scan_id}/group-summaries?filter=exact&sort=files",
        headers={"X-AntiDupeImage-Token": "test-token"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["filter"] == "exact"
    assert payload["sort"] == "files"
    assert payload["groups"][0]["image_count"] == 2
    assert payload["groups"][0]["moved_count"] == 1
    assert payload["groups"][0]["recommended_keep_path"].endswith("first.png")


def test_group_detail_revalidates_snapshot_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, storage = make_app(tmp_path, monkeypatch)
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    scan_id, group_id, scan_images = create_exact_group(storage, image_dir)
    calls: list[int] = []

    def status_for_snapshot(
        snapshot: dict[str, Any], _scan_path: str, *, verify_digest: bool
    ) -> SnapshotFileStatus:
        assert verify_digest is True
        calls.append(int(snapshot["id"]))
        if int(snapshot["id"]) == scan_images[1]:
            return SnapshotFileStatus("moved", False)
        return SnapshotFileStatus("available", True)

    monkeypatch.setattr(operations, "snapshot_file_status", status_for_snapshot)

    response = request(
        app,
        "GET",
        f"/api/scans/{scan_id}/groups/{group_id}",
        headers={"X-AntiDupeImage-Token": "test-token"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert {image["id"] for image in payload["images"]} == set(scan_images)
    moved = next(image for image in payload["images"] if image["id"] == scan_images[1])
    assert moved["status"] == "moved"
    assert moved["available"] is False
    assert sorted(calls) == sorted(scan_images)


def test_group_detail_reports_changed_decoded_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, storage = make_app(tmp_path, monkeypatch)
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    scan_id, group_id, scan_images = create_exact_group(storage, image_dir)
    second_path = image_dir / "second.png"
    snapshot = require_row(storage.get_scan_image(scan_images[1]))
    original = second_path.read_bytes()
    second_path.write_bytes(bytes([original[0] ^ 0xFF]) + original[1:])
    os.utime(second_path, ns=(int(snapshot["mtime_ns"]), int(snapshot["mtime_ns"])))

    response = request(
        app,
        "GET",
        f"/api/scans/{scan_id}/groups/{group_id}",
        headers={"X-AntiDupeImage-Token": "test-token"},
    )

    assert response.status_code == 200
    changed = next(
        image for image in response.json()["images"] if image["id"] == scan_images[1]
    )
    assert changed["status"] == "changed"
    assert changed["available"] is False


def test_thumbnail_uses_descriptor_bound_snapshot_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, storage = make_app(tmp_path, monkeypatch)
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    image_path = image_dir / "image.png"
    Image.new("RGB", (20, 20), (10, 20, 30)).save(image_path)
    scan_id = storage.create_scan(str(image_dir), True, "model", "pretrained", "cpu", {})
    feature = extract_image_features(image_path)
    cache_id = storage.upsert_image(feature)
    snapshot_id = storage.create_scan_image(scan_id, cache_id, feature)
    original_render = routes.render_verified_snapshot_rgb

    def replace_before_open(snapshot: dict[str, Any]) -> Any:
        replacement = image_dir / "replacement.png"
        Image.new("RGB", (20, 20), (90, 80, 70)).save(replacement)
        image_path.unlink()
        replacement.rename(image_path)
        return original_render(snapshot)

    monkeypatch.setattr(routes, "render_verified_snapshot_rgb", replace_before_open)

    response = request(
        app,
        "GET",
        f"/api/images/{snapshot_id}/thumbnail",
        headers={"X-AntiDupeImage-Token": "test-token"},
    )

    assert response.status_code == 409
    assert "identity changed" in response.json()["detail"].lower()


def test_thumbnail_uses_etag_but_requires_revalidation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, storage = make_app(tmp_path, monkeypatch)
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    image_path = image_dir / "image.png"
    Image.new("RGB", (20, 20), (10, 20, 30)).save(image_path)
    scan_id = storage.create_scan(str(image_dir), True, "model", "pretrained", "cpu", {})
    feature = extract_image_features(image_path)
    cache_id = storage.upsert_image(feature)
    snapshot_id = storage.create_scan_image(scan_id, cache_id, feature)
    headers = {"X-AntiDupeImage-Token": "test-token"}

    first = request(app, "GET", f"/api/images/{snapshot_id}/thumbnail", headers=headers)
    second = request(
        app,
        "GET",
        f"/api/images/{snapshot_id}/thumbnail",
        headers={**headers, "If-None-Match": first.headers["etag"]},
    )

    assert first.status_code == 200
    assert first.headers["cache-control"] == "private, no-cache"
    assert second.status_code == 304
    assert second.headers["cache-control"] == "private, no-cache"


def test_thumbnail_cache_reuses_rendered_bytes_but_revalidates_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, storage = make_app(tmp_path, monkeypatch)
    routes.clear_thumbnail_cache()
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    image_path = image_dir / "image.png"
    Image.new("RGB", (20, 20), (10, 20, 30)).save(image_path)
    scan_id = storage.create_scan(str(image_dir), True, "model", "pretrained", "cpu", {})
    feature = extract_image_features(image_path)
    cache_id = storage.upsert_image(feature)
    snapshot_id = storage.create_scan_image(scan_id, cache_id, feature)
    status_calls: list[bool] = []
    render_calls = 0
    original_render = routes.render_verified_snapshot_rgb

    def status(
        _snapshot: dict[str, Any], _scan_path: str, *, verify_digest: bool
    ) -> SnapshotFileStatus:
        status_calls.append(verify_digest)
        return SnapshotFileStatus("available", True)

    def render(snapshot: dict[str, Any]) -> Any:
        nonlocal render_calls
        render_calls += 1
        return original_render(snapshot)

    monkeypatch.setattr(routes, "snapshot_file_status", status)
    monkeypatch.setattr(routes, "render_verified_snapshot_rgb", render)

    headers = {"X-AntiDupeImage-Token": "test-token"}
    first = request(app, "GET", f"/api/images/{snapshot_id}/thumbnail", headers=headers)
    second = request(app, "GET", f"/api/images/{snapshot_id}/thumbnail", headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.content == second.content
    assert render_calls == 1
    assert status_calls == [False, False, True]


def test_sqlite_operational_error_is_returned_without_internal_details(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, _ = make_app(tmp_path, monkeypatch)

    class BrokenStorage:
        def get_scan(self, _scan_id: int) -> None:
            raise sqlite3.OperationalError("database is locked: /private/path/api-token")

    monkeypatch.setattr(operations, "get_storage", lambda: BrokenStorage())

    response = request(
        app,
        "GET",
        "/api/scans/1",
        headers={"X-AntiDupeImage-Token": "test-token"},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "Local database is busy. Retry shortly."}


def test_move_duplicates_api_requires_explicit_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, _ = make_app(tmp_path, monkeypatch)

    response = request(
        app,
        "POST",
        "/api/files/move-duplicates",
        headers={"X-AntiDupeImage-Token": "test-token"},
        json={"scan_id": 1, "group_id": 1, "image_ids": [], "confirm": False},
    )

    assert response.status_code == 400


def test_move_duplicates_api_rejects_duplicate_and_non_positive_image_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, _ = make_app(tmp_path, monkeypatch)
    headers = {"X-AntiDupeImage-Token": "test-token"}

    duplicate_response = request(
        app,
        "POST",
        "/api/files/move-duplicates",
        headers=headers,
        json={"scan_id": 1, "group_id": 1, "image_ids": [1, 1], "confirm": True},
    )
    non_positive_response = request(
        app,
        "POST",
        "/api/files/move-duplicates",
        headers=headers,
        json={"scan_id": 1, "group_id": 1, "image_ids": [0], "confirm": True},
    )

    assert duplicate_response.status_code == 422
    assert non_positive_response.status_code == 422


def test_move_duplicates_api_creates_folder_and_accepts_partial_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, storage = make_app(tmp_path, monkeypatch)
    routes.clear_thumbnail_cache()
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    first_path = image_dir / "first.png"
    second_path = image_dir / "second.png"
    Image.new("RGB", (20, 20), (10, 20, 30)).save(first_path)
    Image.new("RGB", (20, 20), (10, 20, 30)).save(second_path)
    scan_id = storage.create_scan(str(image_dir), True, "model", "pretrained", "cpu", {})
    scan_images: list[int] = []
    for path in (first_path, second_path):
        feature = extract_image_features(path)
        cache_id = storage.upsert_image(feature)
        scan_images.append(storage.create_scan_image(scan_id, cache_id, feature))
    other_scan_id = storage.create_scan(str(image_dir), True, "model", "pretrained", "cpu", {})
    other_scan_images: list[int] = []
    for path in (first_path, second_path):
        feature = extract_image_features(path)
        cache_id = storage.upsert_image(feature)
        other_scan_images.append(storage.create_scan_image(other_scan_id, cache_id, feature))
    storage.create_groups(
        scan_id,
        [
            {
                "score": 1.0,
                "reason": "exact-pixel",
                "recommended_keep_id": scan_images[0],
                "members": [
                    {"image_id": scan_images[0], "score": 1.0, "reason": "recommended-keep"},
                    {"image_id": scan_images[1], "score": 1.0, "reason": "exact-pixel"},
                ],
            }
        ],
    )
    storage.create_groups(
        other_scan_id,
        [
            {
                "score": 1.0,
                "reason": "exact-pixel",
                "recommended_keep_id": other_scan_images[0],
                "members": [
                    {
                        "image_id": other_scan_images[0],
                        "score": 1.0,
                        "reason": "recommended-keep",
                    },
                    {"image_id": other_scan_images[1], "score": 1.0, "reason": "exact-pixel"},
                ],
            }
        ],
    )
    headers = {"X-AntiDupeImage-Token": "test-token"}
    thumbnail_response = request(
        app,
        "GET",
        f"/api/images/{scan_images[1]}/thumbnail",
        headers=headers,
    )

    assert thumbnail_response.status_code == 200
    assert routes.get_thumbnail_cache_size() > 0

    all_response = request(
        app,
        "POST",
        "/api/files/move-duplicates",
        headers=headers,
        json={
            "scan_id": scan_id,
            "group_id": 1,
            "image_ids": scan_images,
            "confirm": True,
        },
    )
    partial_response = request(
        app,
        "POST",
        "/api/files/move-duplicates",
        headers=headers,
        json={
            "scan_id": scan_id,
            "group_id": 1,
            "image_ids": [scan_images[1]],
            "confirm": True,
        },
    )

    assert all_response.status_code == 400
    assert partial_response.status_code == 200
    payload = partial_response.json()
    assert payload["moved"] == [scan_images[1]]
    destination_folder = Path(payload["destination_folder"])
    assert destination_folder.parent == image_dir
    assert destination_folder.name.startswith("AntiDupeImage_Duplicates_")
    assert (destination_folder / "second.png").is_file()
    assert not second_path.exists()
    assert storage.count_groups(scan_id) == 0
    scan = require_row(storage.get_scan(scan_id))
    other_scan = require_row(storage.get_scan(other_scan_id))
    assert scan["duplicate_groups"] == 0
    assert scan["progress_detail"] == "Completed with 0 duplicate group(s)"
    assert require_row(storage.get_scan_image(other_scan_images[1]))["trashed"] is True
    assert storage.count_groups(other_scan_id) == 0
    assert other_scan["duplicate_groups"] == 0
    assert other_scan["progress_detail"] == "Completed with 0 duplicate group(s)"
    assert routes.get_thumbnail_cache_size() == 0


def test_move_duplicates_api_revalidates_large_selection_linearly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, storage = make_app(tmp_path, monkeypatch)
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    paths = [image_dir / f"image_{index:03}.png" for index in range(20)]
    for path in paths:
        Image.new("RGB", (20, 20), (10, 20, 30)).save(path)
    scan_id = storage.create_scan(str(image_dir), True, "model", "pretrained", "cpu", {})
    scan_images: list[int] = []
    for path in paths:
        feature = extract_image_features(path)
        cache_id = storage.upsert_image(feature)
        scan_images.append(storage.create_scan_image(scan_id, cache_id, feature))
    storage.create_groups(
        scan_id,
        [
            {
                "score": 1.0,
                "reason": "exact-pixel",
                "recommended_keep_id": scan_images[0],
                "members": [
                    {
                        "image_id": image_id,
                        "score": 1.0,
                        "reason": "recommended-keep" if image_id == scan_images[0] else "exact-pixel",
                    }
                    for image_id in scan_images
                ],
            }
        ],
    )
    original_status = operations.snapshot_file_status
    status_calls = {"total": 0, "digest": 0}

    def counted_status(
        snapshot: dict[str, Any], scan_path: str, *, verify_digest: bool
    ) -> SnapshotFileStatus:
        status_calls["total"] += 1
        if verify_digest:
            status_calls["digest"] += 1
        return original_status(snapshot, scan_path, verify_digest=verify_digest)

    monkeypatch.setattr(operations, "snapshot_file_status", counted_status)
    selected_ids = scan_images[1:]

    response = request(
        app,
        "POST",
        "/api/files/move-duplicates",
        headers={"X-AntiDupeImage-Token": "test-token"},
        json={
            "scan_id": scan_id,
            "group_id": 1,
            "image_ids": selected_ids,
            "confirm": True,
        },
    )

    assert response.status_code == 200
    assert response.json()["moved"] == selected_ids
    assert status_calls["digest"] == len(selected_ids) * 3
    assert status_calls["total"] <= len(scan_images) + (len(selected_ids) * 4)


def test_move_duplicates_api_does_not_count_changed_keep_as_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, storage = make_app(tmp_path, monkeypatch)
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    scan_id, _, scan_images = create_exact_group(storage, image_dir)
    keep_path = image_dir / "first.png"
    duplicate_path = image_dir / "second.png"
    snapshot = require_row(storage.get_scan_image(scan_images[0]))
    original = keep_path.read_bytes()
    keep_path.write_bytes(bytes([original[0] ^ 0xFF]) + original[1:])
    os.utime(keep_path, ns=(int(snapshot["mtime_ns"]), int(snapshot["mtime_ns"])))

    response = request(
        app,
        "POST",
        "/api/files/move-duplicates",
        headers={"X-AntiDupeImage-Token": "test-token"},
        json={
            "scan_id": scan_id,
            "group_id": 1,
            "image_ids": [scan_images[1]],
            "confirm": True,
        },
    )

    payload = response.json()
    assert response.status_code == 200
    assert payload["moved"] == []
    assert payload["skipped"] == [scan_images[1]]
    assert payload["failures"][0]["reason"] == "Refusing to move the last currently available image in the group"
    assert duplicate_path.exists()


def test_move_duplicates_api_uses_unique_destination_for_name_collision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, storage = make_app(tmp_path, monkeypatch)
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    first_path = image_dir / "first.png"
    second_path = image_dir / "second.png"
    Image.new("RGB", (20, 20), (10, 20, 30)).save(first_path)
    Image.new("RGB", (20, 20), (10, 20, 30)).save(second_path)
    scan_id = storage.create_scan(str(image_dir), True, "model", "pretrained", "cpu", {})
    scan_images: list[int] = []
    for path in (first_path, second_path):
        feature = extract_image_features(path)
        cache_id = storage.upsert_image(feature)
        scan_images.append(storage.create_scan_image(scan_id, cache_id, feature))
    storage.create_groups(
        scan_id,
        [
            {
                "score": 1.0,
                "reason": "exact-pixel",
                "recommended_keep_id": scan_images[0],
                "members": [
                    {"image_id": scan_images[0], "score": 1.0, "reason": "recommended-keep"},
                    {"image_id": scan_images[1], "score": 1.0, "reason": "exact-pixel"},
                ],
            }
        ],
    )
    def fixed_destination(_scan_path: str) -> Path:
        destination = image_dir / "AntiDupeImage_Duplicates_fixed"
        destination.mkdir(exist_ok=True)
        return destination

    existing = image_dir / "AntiDupeImage_Duplicates_fixed" / "second.png"
    existing.parent.mkdir()
    Image.new("RGB", (20, 20), (90, 80, 70)).save(existing)
    monkeypatch.setattr(operations, "create_duplicate_move_root", fixed_destination)

    response = request(
        app,
        "POST",
        "/api/files/move-duplicates",
        headers={"X-AntiDupeImage-Token": "test-token"},
        json={
            "scan_id": scan_id,
            "group_id": 1,
            "image_ids": [scan_images[1]],
            "confirm": True,
        },
    )

    payload = response.json()
    assert response.status_code == 200
    assert payload["moved"] == [scan_images[1]]
    assert existing.is_file()
    assert (existing.parent / "second_2.png").is_file()
    assert not second_path.exists()
    assert require_row(storage.get_scan_image(scan_images[1]))["trashed"] is True
    assert storage.count_groups(scan_id) == 0


def test_move_duplicates_api_keeps_last_image_if_unselected_file_disappears(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, storage = make_app(tmp_path, monkeypatch)
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    first_path = image_dir / "first.png"
    second_path = image_dir / "second.png"
    Image.new("RGB", (20, 20), (10, 20, 30)).save(first_path)
    Image.new("RGB", (20, 20), (10, 20, 30)).save(second_path)
    scan_id = storage.create_scan(str(image_dir), True, "model", "pretrained", "cpu", {})
    scan_images: list[int] = []
    for path in (first_path, second_path):
        feature = extract_image_features(path)
        cache_id = storage.upsert_image(feature)
        scan_images.append(storage.create_scan_image(scan_id, cache_id, feature))
    storage.create_groups(
        scan_id,
        [
            {
                "score": 1.0,
                "reason": "exact-pixel",
                "recommended_keep_id": scan_images[0],
                "members": [
                    {"image_id": scan_images[0], "score": 1.0, "reason": "recommended-keep"},
                    {"image_id": scan_images[1], "score": 1.0, "reason": "exact-pixel"},
                ],
            }
        ],
    )
    original_status = operations.snapshot_file_status
    status_calls = 0

    def remove_keep_before_final_check(
        snapshot: dict[str, Any], scan_path: str, *, verify_digest: bool
    ) -> SnapshotFileStatus:
        nonlocal status_calls
        status_calls += 1
        if status_calls == 3:
            first_path.unlink()
        return original_status(snapshot, scan_path, verify_digest=verify_digest)

    monkeypatch.setattr(operations, "snapshot_file_status", remove_keep_before_final_check)

    response = request(
        app,
        "POST",
        "/api/files/move-duplicates",
        headers={"X-AntiDupeImage-Token": "test-token"},
        json={
            "scan_id": scan_id,
            "group_id": 1,
            "image_ids": [scan_images[1]],
            "confirm": True,
        },
    )

    assert response.status_code == 200
    assert response.json()["moved"] == []
    assert response.json()["skipped"] == [scan_images[1]]
    assert second_path.exists()


def test_move_duplicates_api_rolls_back_if_survivor_disappears_after_move(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, storage = make_app(tmp_path, monkeypatch)
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    scan_id, group_id, scan_images = create_exact_group(storage, image_dir)
    keep_path = image_dir / "first.png"
    duplicate_path = image_dir / "second.png"
    original_status = operations.snapshot_file_status
    keep_digest_checks = 0

    def remove_keep_after_first_survivor_check(
        snapshot: dict[str, Any], scan_path: str, *, verify_digest: bool
    ) -> SnapshotFileStatus:
        nonlocal keep_digest_checks
        if int(snapshot["id"]) == scan_images[0] and verify_digest:
            keep_digest_checks += 1
            if keep_digest_checks == 2:
                keep_path.unlink()
        return original_status(snapshot, scan_path, verify_digest=verify_digest)

    monkeypatch.setattr(
        operations, "snapshot_file_status", remove_keep_after_first_survivor_check
    )

    response = request(
        app,
        "POST",
        "/api/files/move-duplicates",
        headers={"X-AntiDupeImage-Token": "test-token"},
        json={
            "scan_id": scan_id,
            "group_id": group_id,
            "image_ids": [scan_images[1]],
            "confirm": True,
        },
    )

    payload = response.json()
    assert response.status_code == 200
    assert payload["moved"] == []
    assert payload["skipped"] == [scan_images[1]]
    assert "was restored" in payload["failures"][0]["reason"]
    assert duplicate_path.is_file()
    assert require_row(storage.get_scan_image(scan_images[1]))["trashed"] is False
    assert list(Path(payload["destination_folder"]).rglob("*.png")) == []


def test_move_duplicates_api_rolls_back_when_mark_moved_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, storage = make_app(tmp_path, monkeypatch)
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    scan_id, group_id, scan_images = create_exact_group(storage, image_dir)
    duplicate_path = image_dir / "second.png"

    def fail_mark_moved(_image_ids: list[int]) -> list[int]:
        raise sqlite3.OperationalError("simulated database failure")

    monkeypatch.setattr(storage, "mark_moved_and_prune", fail_mark_moved)

    response = request(
        app,
        "POST",
        "/api/files/move-duplicates",
        headers={"X-AntiDupeImage-Token": "test-token"},
        json={
            "scan_id": scan_id,
            "group_id": group_id,
            "image_ids": [scan_images[1]],
            "confirm": True,
        },
    )

    payload = response.json()
    assert response.status_code == 200
    assert payload["moved"] == []
    assert payload["skipped"] == [scan_images[1]]
    assert "Database update failed; the file was restored" in payload["failures"][0]["reason"]
    assert duplicate_path.is_file()
    assert require_row(storage.get_scan_image(scan_images[1]))["trashed"] is False
    assert list(Path(payload["destination_folder"]).rglob("*.png")) == []


def test_move_duplicates_api_rolls_back_mark_when_prune_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, storage = make_app(tmp_path, monkeypatch)
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    scan_id, group_id, scan_images = create_exact_group(storage, image_dir)
    duplicate_path = image_dir / "second.png"

    def fail_prune(_conn: sqlite3.Connection, _scan_id: int) -> list[int]:
        raise sqlite3.OperationalError("simulated prune failure")

    monkeypatch.setattr(storage, "_prune_resolved_groups_in_connection", fail_prune)

    response = request(
        app,
        "POST",
        "/api/files/move-duplicates",
        headers={"X-AntiDupeImage-Token": "test-token"},
        json={
            "scan_id": scan_id,
            "group_id": group_id,
            "image_ids": [scan_images[1]],
            "confirm": True,
        },
    )

    payload = response.json()
    assert response.status_code == 200
    assert payload["moved"] == []
    assert payload["skipped"] == [scan_images[1]]
    assert "Database update failed; the file was restored" in payload["failures"][0]["reason"]
    assert duplicate_path.is_file()
    assert require_row(storage.get_scan_image(scan_images[1]))["trashed"] is False
    assert storage.count_groups(scan_id) == 1
    assert require_row(storage.get_scan(scan_id))["duplicate_groups"] == 1
    assert list(Path(payload["destination_folder"]).rglob("*.png")) == []


def test_move_duplicates_api_revalidates_claim_before_database_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, storage = make_app(tmp_path, monkeypatch)
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    scan_id, group_id, scan_images = create_exact_group(storage, image_dir)
    duplicate_snapshot = require_row(storage.get_scan_image(scan_images[1]))
    original_survivor_check = operations._has_available_unselected_survivor
    survivor_checks = 0
    database_updates = 0
    original_mark_and_prune = storage.mark_moved_and_prune

    def replace_claim_during_second_survivor_check(*args: Any, **kwargs: Any) -> bool:
        nonlocal survivor_checks
        survivor_checks += 1
        result = original_survivor_check(*args, **kwargs)
        if survivor_checks == 2:
            claimed_paths = list(image_dir.glob("AntiDupeImage_Duplicates_*/second.png"))
            assert len(claimed_paths) == 1
            claimed_paths[0].unlink()
            Image.new("RGB", (20, 20), (10, 20, 30)).save(claimed_paths[0])
        return result

    def counted_mark_and_prune(image_ids: list[int]) -> list[int]:
        nonlocal database_updates
        database_updates += 1
        return original_mark_and_prune(image_ids)

    monkeypatch.setattr(
        operations,
        "_has_available_unselected_survivor",
        replace_claim_during_second_survivor_check,
    )
    monkeypatch.setattr(storage, "mark_moved_and_prune", counted_mark_and_prune)

    response = request(
        app,
        "POST",
        "/api/files/move-duplicates",
        headers={"X-AntiDupeImage-Token": "test-token"},
        json={
            "scan_id": scan_id,
            "group_id": group_id,
            "image_ids": [scan_images[1]],
            "confirm": True,
        },
    )

    payload = response.json()
    assert response.status_code == 200
    assert database_updates == 0
    assert payload["moved"] == []
    assert payload["skipped"] == [scan_images[1]]
    assert "Destination integrity failed" in payload["failures"][0]["reason"]
    assert routes.snapshot_file_status(
        duplicate_snapshot,
        str(image_dir),
        verify_digest=True,
    ).available
    assert require_row(storage.get_scan_image(scan_images[1]))["trashed"] is False


def test_move_duplicates_api_discards_copy_if_original_reappears(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, storage = make_app(tmp_path, monkeypatch)
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    scan_id, group_id, scan_images = create_exact_group(storage, image_dir)
    duplicate_path = image_dir / "second.png"
    original_status = operations.snapshot_file_status
    recreated = False

    def recreate_original_after_move(
        snapshot: dict[str, Any], scan_path: str, *, verify_digest: bool
    ) -> SnapshotFileStatus:
        nonlocal recreated
        if (
            int(snapshot["id"]) == scan_images[1]
            and not verify_digest
            and not duplicate_path.exists()
        ):
            moved_paths = list(image_dir.glob("AntiDupeImage_Duplicates_*/second.png"))
            if moved_paths:
                os.link(moved_paths[0], duplicate_path)
                recreated = True
        return original_status(snapshot, scan_path, verify_digest=verify_digest)

    monkeypatch.setattr(operations, "snapshot_file_status", recreate_original_after_move)

    response = request(
        app,
        "POST",
        "/api/files/move-duplicates",
        headers={"X-AntiDupeImage-Token": "test-token"},
        json={
            "scan_id": scan_id,
            "group_id": group_id,
            "image_ids": [scan_images[1]],
            "confirm": True,
        },
    )

    payload = response.json()
    assert response.status_code == 200
    assert recreated
    assert payload["moved"] == []
    assert payload["skipped"] == [scan_images[1]]
    assert duplicate_path.is_file()
    assert require_row(storage.get_scan_image(scan_images[1]))["trashed"] is False
    assert list(Path(payload["destination_folder"]).rglob("*.png")) == []


def test_legacy_trash_endpoint_moves_duplicates_to_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, storage = make_app(tmp_path, monkeypatch)
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    first_path = image_dir / "first.png"
    second_path = image_dir / "second.png"
    Image.new("RGB", (20, 20), (10, 20, 30)).save(first_path)
    Image.new("RGB", (20, 20), (10, 20, 30)).save(second_path)
    scan_id = storage.create_scan(str(image_dir), True, "model", "pretrained", "cpu", {})
    scan_images: list[int] = []
    for path in (first_path, second_path):
        feature = extract_image_features(path)
        cache_id = storage.upsert_image(feature)
        scan_images.append(storage.create_scan_image(scan_id, cache_id, feature))
    storage.create_groups(
        scan_id,
        [
            {
                "score": 1.0,
                "reason": "exact-pixel",
                "recommended_keep_id": scan_images[0],
                "members": [
                    {"image_id": scan_images[0], "score": 1.0, "reason": "recommended-keep"},
                    {"image_id": scan_images[1], "score": 1.0, "reason": "exact-pixel"},
                ],
            }
        ],
    )

    response = request(
        app,
        "POST",
        "/api/files/trash",
        headers={"X-AntiDupeImage-Token": "test-token"},
        json={
            "scan_id": scan_id,
            "group_id": 1,
            "image_ids": [scan_images[1]],
            "confirm": True,
        },
    )

    payload = response.json()
    assert response.status_code == 200
    assert payload["moved"] == [scan_images[1]]
    assert payload["destination_folder"]
    assert (Path(payload["destination_folder"]) / "second.png").is_file()
    assert not second_path.exists()


def test_legacy_trash_endpoint_is_marked_deprecated_in_openapi(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, _ = make_app(tmp_path, monkeypatch)

    operation = app.openapi()["paths"]["/api/files/trash"]["post"]

    assert operation["deprecated"] is True
    assert "Compatibility alias" in operation["description"]
    assert "not OS trash" in operation["description"]

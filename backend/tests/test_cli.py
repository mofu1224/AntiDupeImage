from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.cli import (
    EXIT_ERROR,
    EXIT_NOT_FOUND,
    EXIT_OK,
    main,
)
from app.core.db import Storage
from app.services import operations
from app.services.features import extract_image_features
from app.services.scanner import scan_manager

Image = pytest.importorskip("PIL.Image")


def use_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Storage:
    storage = Storage(tmp_path / "cli.sqlite3")
    storage.init_schema()
    monkeypatch.setattr(operations, "get_storage", lambda: storage)
    scan_manager.storage = storage
    return storage


def seed_exact_group(storage: Storage, image_dir: Path) -> tuple[int, int, list[int]]:
    image_dir.mkdir(parents=True, exist_ok=True)
    first_path = image_dir / "first.png"
    second_path = image_dir / "second.png"
    Image.new("RGB", (24, 24), (10, 20, 30)).save(first_path)
    Image.new("RGB", (24, 24), (10, 20, 30)).save(second_path)
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


def run_cli(*argv: str, capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_cli_initializes_schema_on_first_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    storage = Storage(tmp_path / "fresh.sqlite3")
    monkeypatch.setattr(operations, "get_storage", lambda: storage)
    scan_manager.storage = storage

    code, out, _ = run_cli("scan", "list", "--json", capsys=capsys)

    assert code == EXIT_OK
    assert json.loads(out)["total"] == 0


def test_cli_system_reports_storage_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    use_storage(tmp_path, monkeypatch)

    code, out, _ = run_cli("system", "--json", capsys=capsys)

    assert code == EXIT_OK
    payload = json.loads(out)
    assert payload["device"]["selected"] == "cpu"
    assert payload["db_path"].endswith("antidupeimage.sqlite3")


def test_cli_scan_start_scans_real_images(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    storage = use_storage(tmp_path, monkeypatch)
    image_dir = tmp_path / "photos"
    image_dir.mkdir()
    Image.new("RGB", (32, 32), (200, 40, 40)).save(image_dir / "a.png")
    Image.new("RGB", (32, 32), (200, 40, 40)).save(image_dir / "b.png")
    Image.new("RGB", (32, 32), (40, 40, 200)).save(image_dir / "c.png")

    code, out, _ = run_cli(
        "scan",
        "start",
        str(image_dir),
        "--device",
        "cpu",
        "--precision",
        "exact_pixel",
        "--timeout",
        "120",
        "--json",
        capsys=capsys,
    )

    assert code == EXIT_OK
    payload = json.loads(out)
    assert payload["status"] == "completed"
    assert payload["duplicate_groups"] == 1
    assert storage.count_groups(payload["id"]) == 1


def test_cli_groups_group_and_failures_share_webui_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    storage = use_storage(tmp_path, monkeypatch)
    scan_id, group_id, scan_images = seed_exact_group(storage, tmp_path / "images")

    code, out, _ = run_cli("groups", str(scan_id), "--json", capsys=capsys)
    assert code == EXIT_OK
    summaries = json.loads(out)
    assert summaries["total"] == 1
    assert summaries["groups"][0]["image_count"] == 2

    code, out, _ = run_cli("group", str(scan_id), str(group_id), capsys=capsys)
    assert code == EXIT_OK
    assert f"[{scan_images[0]}]" in out
    assert f"[{scan_images[1]}]" in out

    code, out, _ = run_cli("failures", str(scan_id), "--json", capsys=capsys)
    assert code == EXIT_OK
    assert json.loads(out)["total"] == 0


def test_cli_move_requires_confirmation_and_moves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    storage = use_storage(tmp_path, monkeypatch)
    scan_id, group_id, scan_images = seed_exact_group(storage, tmp_path / "images")

    with pytest.raises(SystemExit) as excinfo:
        main(
            [
                "move",
                str(scan_id),
                str(group_id),
                str(scan_images[1]),
            ]
        )
    assert excinfo.value.code == 2

    code, out, _ = run_cli(
        "move",
        str(scan_id),
        str(group_id),
        str(scan_images[1]),
        "--confirm",
        "--json",
        capsys=capsys,
    )

    assert code == EXIT_OK
    payload = json.loads(out)
    assert payload["moved"] == [scan_images[1]]
    assert payload["destination_folder"] is not None
    assert (tmp_path / "images" / "second.png").exists() is False
    assert len(list(Path(payload["destination_folder"]).glob("*.png"))) == 1


def test_cli_move_refuses_last_available_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    storage = use_storage(tmp_path, monkeypatch)
    scan_id, group_id, scan_images = seed_exact_group(storage, tmp_path / "images")

    code, _, err = run_cli(
        "move",
        str(scan_id),
        str(group_id),
        *[str(image_id) for image_id in scan_images],
        "--confirm",
        "--json",
        capsys=capsys,
    )

    assert code == EXIT_ERROR
    assert err != ""
    assert (tmp_path / "images" / "first.png").is_file()
    assert (tmp_path / "images" / "second.png").is_file()


def test_cli_reports_missing_scan_with_not_found_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    use_storage(tmp_path, monkeypatch)

    code, _, err = run_cli("scan", "show", "9999", capsys=capsys)

    assert code == EXIT_NOT_FOUND
    assert "Scan not found" in err


def test_cli_rejects_invalid_scan_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    use_storage(tmp_path, monkeypatch)

    code, _, err = run_cli(
        "scan", "start", str(tmp_path / "missing"), "--timeout", "5", capsys=capsys
    )

    assert code == EXIT_ERROR
    assert err != ""


def test_cli_scan_list_show_cancel_delete_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    storage = use_storage(tmp_path, monkeypatch)
    scan_id, _, _ = seed_exact_group(storage, tmp_path / "images")

    code, out, _ = run_cli("scan", "list", "--json", capsys=capsys)
    assert code == EXIT_OK
    assert json.loads(out)["total"] == 1

    code, out, _ = run_cli("scan", "show", str(scan_id), "--json", capsys=capsys)
    assert code == EXIT_OK
    assert json.loads(out)["id"] == scan_id

    code, out, _ = run_cli("scan", "cancel", str(scan_id), "--json", capsys=capsys)
    assert code == EXIT_OK
    assert json.loads(out)["status"] == "completed"

    code, out, _ = run_cli("scan", "delete", str(scan_id), "--json", capsys=capsys)
    assert code == EXIT_OK
    assert json.loads(out) == {"deleted": True}


def test_cli_thumbnail_and_reset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    storage = use_storage(tmp_path, monkeypatch)
    _, _, scan_images = seed_exact_group(storage, tmp_path / "images")
    destination = tmp_path / "out" / "thumb.jpg"

    code, _, _ = run_cli(
        "thumbnail", str(scan_images[0]), "--out", str(destination), capsys=capsys
    )
    assert code == EXIT_OK
    assert destination.is_file()

    with pytest.raises(SystemExit) as excinfo:
        main(["reset"])
    assert excinfo.value.code == 2

    code, out, _ = run_cli("reset", "--confirm", "--json", capsys=capsys)
    assert code == EXIT_OK
    payload: dict[str, Any] = json.loads(out)
    assert payload["scans_deleted"] == 1


@pytest.mark.parametrize("timeout", ["nan", "inf", "-inf", "-1"])
def test_cli_rejects_invalid_timeout_before_start(timeout, monkeypatch):
    def unexpected_start(request):
        pytest.fail("Invalid timeout must be rejected before starting a scan")

    monkeypatch.setattr(operations, "start_scan", unexpected_start)
    with pytest.raises(SystemExit) as error:
        main(["scan", "start", "unused", f"--timeout={timeout}"])
    assert error.value.code == 2


def test_cli_interrupt_cancels_and_shuts_down_console_entrypoint(monkeypatch, capsys):
    from app.models.schemas import ScanResponse

    events = []
    monkeypatch.setattr(operations, "start_scan", lambda request: ScanResponse(id=42, status="queued"))
    monkeypatch.setattr(operations, "cancel_scan", lambda scan_id: events.append(("cancel", scan_id)))
    monkeypatch.setattr(scan_manager, "shutdown", lambda: events.append(("shutdown",)))

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(operations, "wait_for_scan", interrupted)
    assert main(["scan", "start", "unused"]) == 130
    assert events == [("cancel", 42), ("shutdown",)]
    assert "Interrupted" in capsys.readouterr().err


def test_cli_reports_damaged_database_clearly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    database = tmp_path / "damaged.sqlite3"
    database.write_bytes(b"this file is not a database" * 64)
    storage = Storage(database)
    monkeypatch.setattr(operations, "get_storage", lambda: storage)
    scan_manager.storage = storage

    code, _, err = run_cli("scan", "list", capsys=capsys)

    assert code == EXIT_ERROR
    assert "Local database error" in err
    assert "Traceback" not in err


def test_cli_reports_filesystem_errors_clearly(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken_storage() -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(operations, "get_storage", broken_storage)

    code, _, err = run_cli("scan", "list", capsys=capsys)

    assert code == EXIT_ERROR
    assert "No space left on device" in err
    assert "Traceback" not in err

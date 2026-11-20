from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

Image = pytest.importorskip("PIL.Image")

from app.core.db import LEGACY_MIGRATION_WARNING, SCHEMA_VERSION, Storage
from app.services.features import (
    FEATURE_REVISION,
    LIGHTWEIGHT_FEATURE_REVISION,
    extract_image_features,
    extract_lightweight_image_features,
)


def require_row(row: dict[str, Any] | None) -> dict[str, Any]:
    assert row is not None
    return row


def require_lastrowid(cursor: sqlite3.Cursor) -> int:
    assert cursor.lastrowid is not None
    return cursor.lastrowid


def unique_index_columns(conn: sqlite3.Connection, table: str) -> set[tuple[str, ...]]:
    unique_columns: set[tuple[str, ...]] = set()
    for index in conn.execute(f"PRAGMA index_list({table})").fetchall():
        if not index["unique"]:
            continue
        columns = tuple(
            row["name"]
            for row in conn.execute(
                "SELECT name FROM pragma_index_info(?) ORDER BY seqno", (index["name"],)
            ).fetchall()
        )
        unique_columns.add(columns)
    return unique_columns


def index_columns(conn: sqlite3.Connection, table: str) -> set[tuple[str, ...]]:
    columns: set[tuple[str, ...]] = set()
    for index in conn.execute(f"PRAGMA index_list({table})").fetchall():
        names = tuple(
            row["name"]
            for row in conn.execute(
                "SELECT name FROM pragma_index_info(?) ORDER BY seqno",
                (index["name"],),
            ).fetchall()
        )
        columns.add(names)
    return columns


def test_schema_initialization_rolls_back_partial_ddl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "rollback.sqlite3"
    storage = Storage(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE existing_marker (id INTEGER PRIMARY KEY)")

    def broken_schema(conn: sqlite3.Connection) -> None:
        conn.execute("CREATE TABLE partial_schema (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE broken (")

    monkeypatch.setattr(storage, "_create_current_schema", broken_schema)

    with pytest.raises(sqlite3.OperationalError):
        storage.init_schema()

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='existing_marker'"
        ).fetchone()
        assert (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='partial_schema'"
            ).fetchone()
            is None
        )


def test_connect_closes_connection_when_pragma_initialization_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class BrokenConnection:
        row_factory: Any = None
        closed = False

        def execute(self, _statement: str) -> None:
            raise sqlite3.OperationalError("pragma failed")

        def close(self) -> None:
            self.closed = True

    connection = BrokenConnection()
    monkeypatch.setattr(sqlite3, "connect", lambda *args, **kwargs: connection)
    storage = Storage(tmp_path / "db.sqlite3")

    with pytest.raises(sqlite3.OperationalError, match="pragma failed"):
        with storage.connect():
            pass

    assert connection.closed


def test_current_schema_uses_feature_revision_in_image_cache_key(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite3")
    storage.init_schema()

    with storage.connect() as conn:
        keys = unique_index_columns(conn, "images")
        assert ("path", "feature_revision") in keys
        assert ("path",) not in keys


def test_current_schema_indexes_status_order_and_restores_missing_index(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite3")
    storage.init_schema()

    with storage.connect() as conn:
        assert ("status", "id") in index_columns(conn, "scans")
        conn.execute("DROP INDEX idx_scans_status_id")

    storage.init_schema()

    with storage.connect() as conn:
        assert ("status", "id") in index_columns(conn, "scans")


def test_scan_page_count_and_rows_share_one_read_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "db.sqlite3"
    storage = Storage(db_path)
    writer = Storage(db_path)
    storage.init_schema()
    first_id = storage.create_scan(str(tmp_path / "first"), True, "model", "pre", "cpu", {})
    original_connect = storage.connect
    inserted = False

    class InjectingConnection:
        def __init__(self, connection: sqlite3.Connection) -> None:
            self.connection = connection

        def execute(
            self,
            statement: str,
            parameters: tuple[Any, ...] = (),
        ) -> sqlite3.Cursor:
            nonlocal inserted
            cursor = self.connection.execute(statement, parameters)
            if "SELECT COUNT(*) FROM scans" in statement and not inserted:
                inserted = True
                writer.create_scan(
                    str(tmp_path / "second"),
                    True,
                    "model",
                    "pre",
                    "cpu",
                    {},
                )
            return cursor

    @contextmanager
    def injecting_connect():
        with original_connect() as connection:
            yield InjectingConnection(connection)

    monkeypatch.setattr(storage, "connect", injecting_connect)

    rows, total = storage.list_scans(10, 0)

    assert inserted
    assert total == 1
    assert [row["id"] for row in rows] == [first_id]


def test_image_cache_keeps_algorithm_revisions_separate(tmp_path: Path) -> None:
    path = tmp_path / "image.png"
    Image.new("RGB", (20, 20), (10, 20, 30)).save(path)
    storage = Storage(tmp_path / "db.sqlite3")
    storage.init_schema()
    feature = extract_image_features(path)
    old_feature: dict[str, Any] = {**feature, "feature_revision": "old-revision"}

    old_id = storage.upsert_image(old_feature)
    current_id = storage.upsert_image(feature)

    assert current_id != old_id
    assert require_row(storage.get_cached_image(feature["path"], feature, "old-revision"))[
        "id"
    ] == old_id
    assert require_row(storage.get_cached_image(feature["path"], feature, FEATURE_REVISION))[
        "id"
    ] == current_id


def test_scan_image_snapshot_does_not_change_when_cache_row_is_updated(tmp_path: Path) -> None:
    path = tmp_path / "image.png"
    Image.new("RGB", (20, 20), (10, 20, 30)).save(path)
    storage = Storage(tmp_path / "db.sqlite3")
    storage.init_schema()
    scan_id = storage.create_scan(str(tmp_path), True, "model", "pretrained", "cpu", {})

    first = extract_image_features(path)
    image_id = storage.upsert_image(first)
    scan_image_id = storage.create_scan_image(scan_id, image_id, first)
    first_digest = first["pixel_digest"]

    Image.new("RGB", (20, 20), (30, 20, 10)).save(path)
    second = extract_image_features(path)
    storage.upsert_image(second)

    assert require_row(storage.get_image(image_id))["pixel_digest"] == second["pixel_digest"]
    assert require_row(storage.get_scan_image(scan_image_id))["pixel_digest"] == first_digest


def test_upsert_restores_global_cache_row_from_trashed_state(tmp_path: Path) -> None:
    path = tmp_path / "image.png"
    Image.new("RGB", (20, 20), (10, 20, 30)).save(path)
    storage = Storage(tmp_path / "db.sqlite3")
    storage.init_schema()
    feature = extract_image_features(path)
    image_id = storage.upsert_image(feature)
    scan_id = storage.create_scan(str(tmp_path), True, "model", "pretrained", "cpu", {})
    scan_image_id = storage.create_scan_image(scan_id, image_id, feature)
    storage.mark_trashed([scan_image_id])

    storage.upsert_image(feature)
    second_scan_id = storage.create_scan(str(tmp_path), True, "model", "pretrained", "cpu", {})
    second_scan_image_id = storage.create_scan_image(second_scan_id, image_id, feature)

    assert require_row(storage.get_image(image_id))["trashed"] is False
    assert require_row(storage.get_scan_image(second_scan_image_id))["trashed"] is False


def test_feature_cache_requires_current_algorithm_revision(tmp_path: Path) -> None:
    path = tmp_path / "image.png"
    Image.new("RGB", (20, 20), (10, 20, 30)).save(path)
    storage = Storage(tmp_path / "db.sqlite3")
    storage.init_schema()
    feature = extract_image_features(path)
    storage.upsert_image(feature)

    assert storage.get_cached_image(feature["path"], feature, FEATURE_REVISION) is not None
    assert storage.get_cached_image(feature["path"], feature, "old-revision") is None


def test_cached_image_lookup_does_not_rewrite_unchanged_active_rows(tmp_path: Path) -> None:
    path = tmp_path / "image.png"
    Image.new("RGB", (20, 20), (10, 20, 30)).save(path)
    storage = Storage(tmp_path / "db.sqlite3")
    storage.init_schema()
    feature = extract_image_features(path)
    image_id = storage.upsert_image(feature)
    with storage.connect() as conn:
        conn.execute(
            "UPDATE images SET updated_at='2000-01-01 00:00:00' WHERE id=?",
            (image_id,),
        )

    cached = storage.get_cached_images([feature, feature], FEATURE_REVISION)

    assert list(cached) == [str(path)]
    with storage.connect() as conn:
        updated_at = conn.execute(
            "SELECT updated_at FROM images WHERE id=?",
            (image_id,),
        ).fetchone()[0]
    assert updated_at == "2000-01-01 00:00:00"


def test_cached_image_lookup_rolls_back_trashed_reset_when_cancelled(tmp_path: Path) -> None:
    path = tmp_path / "image.png"
    Image.new("RGB", (20, 20), (10, 20, 30)).save(path)
    storage = Storage(tmp_path / "db.sqlite3")
    storage.init_schema()
    feature = extract_image_features(path)
    image_id = storage.upsert_image(feature)
    with storage.connect() as conn:
        conn.execute("UPDATE images SET trashed=1 WHERE id=?", (image_id,))
    checks = 0

    def cancel_after_lookup() -> None:
        nonlocal checks
        checks += 1
        if checks == 2:
            raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        storage.get_cached_images([feature], FEATURE_REVISION, cancel_after_lookup)

    assert require_row(storage.get_image(image_id))["trashed"] is True


def test_clip_embeddings_bulk_round_trip_and_match_full_model_identity(tmp_path: Path) -> None:
    paths = [tmp_path / "first.png", tmp_path / "second.png"]
    for index, path in enumerate(paths):
        Image.new("RGB", (20, 20), (10 + index, 20, 30)).save(path)
    storage = Storage(tmp_path / "db.sqlite3")
    storage.init_schema()
    image_ids = [storage.upsert_image(extract_image_features(path)) for path in paths]

    storage.set_embeddings_bulk(
        "model",
        "pretrained",
        "revision",
        [(image_ids[0], [0.1, 0.2]), (image_ids[1], [0.3, 0.4])],
    )

    embeddings = storage.get_clip_embeddings(
        image_ids + [image_ids[0]],
        "model",
        "pretrained",
        "revision",
    )
    assert embeddings[image_ids[0]] == pytest.approx([0.1, 0.2])
    assert embeddings[image_ids[1]] == pytest.approx([0.3, 0.4])
    assert storage.get_clip_embeddings(image_ids, "other", "pretrained", "revision") == {}
    assert storage.get_clip_embeddings(image_ids, "model", "other", "revision") == {}
    assert storage.get_clip_embeddings(image_ids, "model", "pretrained", "other") == {}


def test_clip_embeddings_bulk_rolls_back_every_update_on_failure(tmp_path: Path) -> None:
    paths = [tmp_path / "first.png", tmp_path / "second.png"]
    for index, path in enumerate(paths):
        Image.new("RGB", (20, 20), (10 + index, 20, 30)).save(path)
    storage = Storage(tmp_path / "db.sqlite3")
    storage.init_schema()
    image_ids = [storage.upsert_image(extract_image_features(path)) for path in paths]
    storage.set_embeddings_bulk(
        "old-model",
        "old-pretrained",
        "old-revision",
        [(image_id, [0.5, 0.5]) for image_id in image_ids],
    )
    with storage.connect() as conn:
        conn.execute(
            f"""
            CREATE TRIGGER reject_second_embedding
            BEFORE UPDATE OF embedding_blob ON images
            WHEN NEW.id={image_ids[1]}
            BEGIN
              SELECT RAISE(ABORT, 'blocked embedding update');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="blocked embedding update"):
        storage.set_embeddings_bulk(
            "new-model",
            "new-pretrained",
            "new-revision",
            [(image_id, [1.0, 0.0]) for image_id in image_ids],
        )

    assert storage.get_clip_embeddings(
        image_ids,
        "new-model",
        "new-pretrained",
        "new-revision",
    ) == {}
    old_embeddings = storage.get_clip_embeddings(
        image_ids,
        "old-model",
        "old-pretrained",
        "old-revision",
    )
    assert set(old_embeddings) == set(image_ids)
    assert all(embedding == pytest.approx([0.5, 0.5]) for embedding in old_embeddings.values())


def test_feature_cache_keeps_standard_and_lightweight_revisions_separate(
    tmp_path: Path,
) -> None:
    path = tmp_path / "image.png"
    Image.new("RGB", (120, 90), (10, 20, 30)).save(path)
    storage = Storage(tmp_path / "db.sqlite3")
    storage.init_schema()

    standard = extract_image_features(path)
    lightweight = extract_lightweight_image_features(path)
    standard_id = storage.upsert_image(standard)
    lightweight_id = storage.upsert_image(lightweight)

    assert standard_id != lightweight_id
    assert require_row(storage.get_image(standard_id))["feature_revision"] == FEATURE_REVISION
    assert (
        require_row(storage.get_image(lightweight_id))["feature_revision"]
        == LIGHTWEIGHT_FEATURE_REVISION
    )
    assert (
        require_row(storage.get_cached_image(standard["path"], standard, FEATURE_REVISION))["id"]
        == standard_id
    )
    assert (
        require_row(
            storage.get_cached_image(lightweight["path"], lightweight, LIGHTWEIGHT_FEATURE_REVISION)
        )["id"]
        == lightweight_id
    )


def test_bulk_scan_image_save_preserves_order_and_populates_cache(tmp_path: Path) -> None:
    paths = [tmp_path / f"{index}.png" for index in range(3)]
    for index, path in enumerate(paths):
        Image.new("RGB", (20, 20), (10 + index, 20, 30)).save(path)
    features = [extract_image_features(path) for path in paths]
    storage = Storage(tmp_path / "db.sqlite3")
    storage.init_schema()
    scan_id = storage.create_scan(str(tmp_path), True, "model", "pretrained", "cpu", {})

    saved_ids = storage.save_scan_images_bulk(scan_id, features)

    assert len(saved_ids) == len(features)
    with storage.connect() as conn:
        rows = conn.execute(
            "SELECT path, image_id FROM scan_images WHERE scan_id=? ORDER BY id ASC",
            (scan_id,),
        ).fetchall()
    assert [row["path"] for row in rows] == [feature["path"] for feature in features]
    assert [row["image_id"] for row in rows] == [image_id for image_id, _ in saved_ids]
    cached = storage.get_cached_images(features, FEATURE_REVISION)
    assert set(cached) == {feature["path"] for feature in features}


def test_bulk_scan_image_save_rolls_back_when_cancelled(tmp_path: Path) -> None:
    paths = [tmp_path / f"{index}.png" for index in range(2)]
    for index, path in enumerate(paths):
        Image.new("RGB", (20, 20), (10 + index, 20, 30)).save(path)
    features = [extract_image_features(path) for path in paths]
    storage = Storage(tmp_path / "db.sqlite3")
    storage.init_schema()
    scan_id = storage.create_scan(str(tmp_path), True, "model", "pretrained", "cpu", {})
    checks = 0

    def cancel_during_save() -> None:
        nonlocal checks
        checks += 1
        if checks == 2:
            raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        storage.save_scan_images_bulk(scan_id, features, cancel_during_save)

    with storage.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM images").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM scan_images").fetchone()[0] == 0


def test_reset_app_data_clears_history_cache_and_resets_ids(tmp_path: Path) -> None:
    path = tmp_path / "image.png"
    Image.new("RGB", (20, 20), (10, 20, 30)).save(path)
    storage = Storage(tmp_path / "db.sqlite3")
    storage.init_schema()
    scan_id = storage.create_scan(str(tmp_path), True, "model", "pretrained", "cpu", {})
    feature = extract_image_features(path)
    image_id, scan_image_id = storage.save_scan_image(scan_id, feature)
    storage.record_failure(scan_id, str(tmp_path / "bad.txt"), "decode", "bad file")
    storage.update_scan(
        scan_id,
        status="completed",
        phase="completed",
        progress_detail="Completed with 0 duplicate group(s)",
        progress=1.0,
    )

    counts = storage.reset_app_data()

    assert counts["scans_deleted"] == 1
    assert counts["scan_images_deleted"] == 1
    assert counts["cached_images_deleted"] == 1
    assert counts["failures_deleted"] == 1
    assert path.is_file()
    assert storage.get_scan(scan_id) is None
    assert storage.get_image(image_id) is None
    assert storage.get_scan_image(scan_image_id) is None
    assert storage.create_scan(str(tmp_path), True, "model", "pretrained", "cpu", {}) == 1
    assert storage.upsert_image(feature) == 1


def test_record_failures_bulk_saves_all_failures(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite3")
    storage.init_schema()
    scan_id = storage.create_scan(str(tmp_path), True, "model", "pretrained", "cpu", {})

    storage.record_failures_bulk(
        scan_id,
        [
            (str(tmp_path / "a.txt"), "discovery", "bad directory"),
            (str(tmp_path / "b.png"), "features", "decode failed"),
        ],
    )

    failures, total = storage.list_failures(scan_id, 10, 0)
    assert total == 2
    assert [(row["path"], row["stage"], row["error"]) for row in failures] == [
        (str(tmp_path / "a.txt"), "discovery", "bad directory"),
        (str(tmp_path / "b.png"), "features", "decode failed"),
    ]


def test_large_windows_file_identity_values_are_stored_and_matched(tmp_path: Path) -> None:
    path = tmp_path / "image.png"
    Image.new("RGB", (20, 20), (10, 20, 30)).save(path)
    feature = extract_image_features(path)
    feature["file_dev"] = 2**63 + 123
    feature["file_ino"] = 2**63 + 456
    storage = Storage(tmp_path / "db.sqlite3")
    storage.init_schema()
    scan_id = storage.create_scan(str(tmp_path), True, "model", "pretrained", "cpu", {})

    image_id, scan_image_id = storage.save_scan_image(scan_id, feature)

    with storage.connect() as conn:
        row = conn.execute(
            """
            SELECT
              typeof(file_dev) AS dev_type,
              typeof(file_ino) AS ino_type,
              file_dev,
              file_ino
            FROM images
            """
        ).fetchone()
    assert row["dev_type"] == "text"
    assert row["ino_type"] == "text"
    assert str(row["file_dev"]).startswith("bigint:")
    assert str(row["file_ino"]).startswith("bigint:")
    cached = require_row(storage.get_cached_image(feature["path"], feature, FEATURE_REVISION))
    assert cached["file_dev"] == feature["file_dev"]
    assert cached["file_ino"] == feature["file_ino"]

    storage.mark_trashed([scan_image_id])

    assert require_row(storage.get_image(image_id))["trashed"] is True
    assert require_row(storage.get_scan_image(scan_image_id))["trashed"] is True


def test_mark_trashed_does_not_change_different_snapshot_at_same_path(tmp_path: Path) -> None:
    path = tmp_path / "image.png"
    storage = Storage(tmp_path / "db.sqlite3")
    storage.init_schema()

    Image.new("RGB", (20, 20), (10, 20, 30)).save(path)
    first = extract_image_features(path)
    image_id = storage.upsert_image(first)
    first_scan = storage.create_scan(str(tmp_path), True, "model", "pretrained", "cpu", {})
    first_snapshot = storage.create_scan_image(first_scan, image_id, first)

    Image.new("RGB", (20, 20), (30, 20, 10)).save(path)
    second = extract_image_features(path)
    image_id = storage.upsert_image(second)
    second_scan = storage.create_scan(str(tmp_path), True, "model", "pretrained", "cpu", {})
    second_snapshot = storage.create_scan_image(second_scan, image_id, second)

    storage.mark_trashed([second_snapshot])

    assert require_row(storage.get_scan_image(first_snapshot))["trashed"] is False
    assert require_row(storage.get_scan_image(second_snapshot))["trashed"] is True


def test_legacy_database_is_migrated_with_rescan_warning(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE scans (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          path TEXT NOT NULL,
          recursive INTEGER NOT NULL,
          model TEXT NOT NULL,
          pretrained TEXT NOT NULL,
          device TEXT NOT NULL,
          thresholds_json TEXT NOT NULL,
          status TEXT NOT NULL,
          progress REAL NOT NULL DEFAULT 0,
          total_files INTEGER NOT NULL DEFAULT 0,
          processed_files INTEGER NOT NULL DEFAULT 0,
          candidate_pairs INTEGER NOT NULL DEFAULT 0,
          duplicate_groups INTEGER NOT NULL DEFAULT 0,
          error TEXT,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE images (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          path TEXT NOT NULL UNIQUE,
          mtime_ns INTEGER NOT NULL,
          file_size INTEGER NOT NULL,
          width INTEGER NOT NULL,
          height INTEGER NOT NULL,
          pixel_digest TEXT NOT NULL,
          phash TEXT NOT NULL,
          phash_int TEXT NOT NULL,
          avg_rgb_json TEXT NOT NULL,
          color_hist_json TEXT NOT NULL,
          hsv_hist_json TEXT NOT NULL,
          clip_model TEXT,
          clip_pretrained TEXT,
          embedding_json TEXT,
          trashed INTEGER NOT NULL DEFAULT 0,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE duplicate_groups (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          scan_id INTEGER NOT NULL,
          score REAL NOT NULL,
          reason TEXT NOT NULL,
          recommended_keep_id INTEGER NOT NULL
        );
        CREATE TABLE group_members (
          group_id INTEGER NOT NULL,
          image_id INTEGER NOT NULL,
          score REAL NOT NULL,
          PRIMARY KEY (group_id, image_id)
        );
        INSERT INTO scans (
          path, recursive, model, pretrained, device, thresholds_json, status, duplicate_groups
        ) VALUES ('/tmp/images', 1, 'model', 'pretrained', 'cpu', '{}', 'completed', 1);
        """
    )
    conn.commit()
    conn.close()

    storage = Storage(db_path)
    storage.init_schema()
    scan = require_row(storage.get_scan(1))
    image_path = tmp_path / "new.png"
    Image.new("RGB", (20, 20), (10, 20, 30)).save(image_path)
    feature = extract_image_features(image_path)
    image_id = storage.upsert_image(feature)
    snapshot_id = storage.create_scan_image(1, image_id, feature)

    assert scan["duplicate_groups"] == 0
    assert scan["warnings"] == [LEGACY_MIGRATION_WARNING]
    assert require_row(storage.get_scan_image(snapshot_id))["pixel_digest"] == feature["pixel_digest"]
    with storage.connect() as migrated:
        assert migrated.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        columns = {row["name"] for row in migrated.execute("PRAGMA table_info(group_members)")}
        assert "reason" in columns
        assert json.loads(
            migrated.execute("SELECT warnings_json FROM scans WHERE id=1").fetchone()[0]
        ) == [LEGACY_MIGRATION_WARNING]


def test_newer_database_schema_is_rejected(tmp_path: Path) -> None:
    db_path = tmp_path / "future.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA user_version=999")
    conn.commit()
    conn.close()

    with pytest.raises(RuntimeError, match="newer than supported"):
        Storage(db_path).init_schema()


def test_schema_version_7_database_is_supported(tmp_path: Path) -> None:
    db_path = tmp_path / "v7.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA user_version=7")
    conn.commit()
    conn.close()

    Storage(db_path).init_schema()

    with sqlite3.connect(db_path) as migrated:
        assert migrated.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_v2_database_adds_feature_revision_columns(tmp_path: Path) -> None:
    db_path = tmp_path / "v2.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        PRAGMA user_version=2;
        CREATE TABLE images (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          path TEXT NOT NULL UNIQUE,
          file_dev INTEGER NOT NULL DEFAULT 0,
          file_ino INTEGER NOT NULL DEFAULT 0,
          mtime_ns INTEGER NOT NULL,
          file_size INTEGER NOT NULL,
          width INTEGER NOT NULL,
          height INTEGER NOT NULL,
          pixel_digest TEXT NOT NULL,
          phash TEXT NOT NULL,
          phash_int TEXT NOT NULL,
          avg_rgb_json TEXT NOT NULL,
          color_hist_json TEXT NOT NULL,
          hsv_hist_json TEXT NOT NULL,
          clip_model TEXT,
          clip_pretrained TEXT,
          clip_revision TEXT,
          embedding_blob BLOB,
          embedding_json TEXT,
          trashed INTEGER NOT NULL DEFAULT 0,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE scan_images (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          scan_id INTEGER NOT NULL,
          image_id INTEGER,
          path TEXT NOT NULL,
          file_dev INTEGER NOT NULL,
          file_ino INTEGER NOT NULL,
          mtime_ns INTEGER NOT NULL,
          file_size INTEGER NOT NULL,
          width INTEGER NOT NULL,
          height INTEGER NOT NULL,
          pixel_digest TEXT NOT NULL,
          phash TEXT NOT NULL,
          phash_int TEXT NOT NULL,
          avg_rgb_json TEXT NOT NULL,
          color_hist_json TEXT NOT NULL,
          hsv_hist_json TEXT NOT NULL,
          trashed INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(scan_id, path)
        );
        """
    )
    conn.commit()
    conn.close()

    storage = Storage(db_path)
    storage.init_schema()

    with storage.connect() as migrated:
        image_columns = {row["name"] for row in migrated.execute("PRAGMA table_info(images)")}
        snapshot_columns = {
            row["name"] for row in migrated.execute("PRAGMA table_info(scan_images)")
        }
        assert "feature_revision" in image_columns
        assert "feature_revision" in snapshot_columns
        assert migrated.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_v3_database_adds_scan_phase_columns(tmp_path: Path) -> None:
    db_path = tmp_path / "v3.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        PRAGMA user_version=3;
        CREATE TABLE scans (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          path TEXT NOT NULL,
          recursive INTEGER NOT NULL,
          model TEXT NOT NULL,
          pretrained TEXT NOT NULL,
          device TEXT NOT NULL,
          thresholds_json TEXT NOT NULL,
          status TEXT NOT NULL,
          progress REAL NOT NULL DEFAULT 0,
          total_files INTEGER NOT NULL DEFAULT 0,
          processed_files INTEGER NOT NULL DEFAULT 0,
          succeeded_files INTEGER NOT NULL DEFAULT 0,
          failed_files INTEGER NOT NULL DEFAULT 0,
          skipped_files INTEGER NOT NULL DEFAULT 0,
          candidate_pairs INTEGER NOT NULL DEFAULT 0,
          duplicate_groups INTEGER NOT NULL DEFAULT 0,
          error TEXT,
          warnings_json TEXT NOT NULL DEFAULT '[]',
          cancel_requested INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO scans (
          path, recursive, model, pretrained, device, thresholds_json, status, error
        ) VALUES
          ('/tmp/done', 1, 'model', 'pretrained', 'cpu', '{}', 'completed', NULL),
          ('/tmp/running', 1, 'model', 'pretrained', 'cpu', '{}', 'running', NULL),
          ('/tmp/failed', 1, 'model', 'pretrained', 'cpu', '{}', 'failed', 'boom');
        CREATE TABLE scan_images (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          scan_id INTEGER NOT NULL,
          image_id INTEGER,
          path TEXT NOT NULL,
          feature_revision TEXT NOT NULL,
          file_dev INTEGER NOT NULL,
          file_ino INTEGER NOT NULL,
          mtime_ns INTEGER NOT NULL,
          file_size INTEGER NOT NULL,
          width INTEGER NOT NULL,
          height INTEGER NOT NULL,
          pixel_digest TEXT NOT NULL,
          phash TEXT NOT NULL,
          phash_int TEXT NOT NULL,
          avg_rgb_json TEXT NOT NULL,
          color_hist_json TEXT NOT NULL,
          hsv_hist_json TEXT NOT NULL,
          trashed INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(scan_id, path)
        );
        """
    )
    conn.commit()
    conn.close()

    storage = Storage(db_path)
    storage.init_schema()

    assert require_row(storage.get_scan(1))["phase"] == "completed"
    assert require_row(storage.get_scan(2))["phase"] == "extracting"
    failed = require_row(storage.get_scan(3))
    assert failed["phase"] == "failed"
    assert failed["progress_detail"] == "boom"
    with storage.connect() as migrated:
        columns = {row["name"] for row in migrated.execute("PRAGMA table_info(scans)")}
        snapshot_columns = {
            row["name"] for row in migrated.execute("PRAGMA table_info(scan_images)")
        }
        assert {"phase", "progress_detail"}.issubset(columns)
        assert {"dhash", "whash", "luma_layout_json", "edge_layout_json"}.issubset(
            snapshot_columns
        )
        assert migrated.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_v6_database_migrates_image_cache_key_to_path_and_revision(
    tmp_path: Path,
) -> None:
    path = tmp_path / "image.png"
    Image.new("RGB", (80, 80), (10, 20, 30)).save(path)
    feature = extract_image_features(path)
    db_path = tmp_path / "v6.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        PRAGMA user_version=6;
        CREATE TABLE images (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          path TEXT NOT NULL UNIQUE,
          feature_revision TEXT NOT NULL,
          file_dev INTEGER NOT NULL DEFAULT 0,
          file_ino INTEGER NOT NULL DEFAULT 0,
          mtime_ns INTEGER NOT NULL,
          file_size INTEGER NOT NULL,
          width INTEGER NOT NULL,
          height INTEGER NOT NULL,
          pixel_digest TEXT NOT NULL,
          phash TEXT NOT NULL,
          phash_int TEXT NOT NULL,
          dhash TEXT NOT NULL DEFAULT '',
          whash TEXT NOT NULL DEFAULT '',
          avg_rgb_json TEXT NOT NULL,
          color_hist_json TEXT NOT NULL,
          hsv_hist_json TEXT NOT NULL,
          luma_layout_json TEXT NOT NULL DEFAULT '[]',
          edge_layout_json TEXT NOT NULL DEFAULT '[]',
          quality_score REAL NOT NULL DEFAULT 0,
          clip_model TEXT,
          clip_pretrained TEXT,
          clip_revision TEXT,
          embedding_blob BLOB,
          embedding_json TEXT,
          trashed INTEGER NOT NULL DEFAULT 0,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE scans (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          path TEXT NOT NULL,
          recursive INTEGER NOT NULL,
          model TEXT NOT NULL,
          pretrained TEXT NOT NULL,
          device TEXT NOT NULL,
          thresholds_json TEXT NOT NULL,
          status TEXT NOT NULL,
          phase TEXT NOT NULL DEFAULT 'queued',
          progress_detail TEXT NOT NULL DEFAULT '',
          progress REAL NOT NULL DEFAULT 0,
          total_files INTEGER NOT NULL DEFAULT 0,
          processed_files INTEGER NOT NULL DEFAULT 0,
          succeeded_files INTEGER NOT NULL DEFAULT 0,
          failed_files INTEGER NOT NULL DEFAULT 0,
          skipped_files INTEGER NOT NULL DEFAULT 0,
          candidate_pairs INTEGER NOT NULL DEFAULT 0,
          duplicate_groups INTEGER NOT NULL DEFAULT 0,
          error TEXT,
          warnings_json TEXT NOT NULL DEFAULT '[]',
          cancel_requested INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE scan_images (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          scan_id INTEGER NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
          image_id INTEGER REFERENCES images(id) ON DELETE SET NULL,
          path TEXT NOT NULL,
          feature_revision TEXT NOT NULL,
          file_dev INTEGER NOT NULL,
          file_ino INTEGER NOT NULL,
          mtime_ns INTEGER NOT NULL,
          file_size INTEGER NOT NULL,
          width INTEGER NOT NULL,
          height INTEGER NOT NULL,
          pixel_digest TEXT NOT NULL,
          phash TEXT NOT NULL,
          phash_int TEXT NOT NULL,
          dhash TEXT NOT NULL DEFAULT '',
          whash TEXT NOT NULL DEFAULT '',
          avg_rgb_json TEXT NOT NULL,
          color_hist_json TEXT NOT NULL,
          hsv_hist_json TEXT NOT NULL,
          luma_layout_json TEXT NOT NULL DEFAULT '[]',
          edge_layout_json TEXT NOT NULL DEFAULT '[]',
          quality_score REAL NOT NULL DEFAULT 0,
          trashed INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(scan_id, path)
        );
        """
    )
    image_row = conn.execute(
        """
        INSERT INTO images (
          path, feature_revision, file_dev, file_ino, mtime_ns, file_size,
          width, height, pixel_digest, phash, phash_int, dhash, whash,
          avg_rgb_json, color_hist_json, hsv_hist_json, luma_layout_json,
          edge_layout_json, quality_score
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            feature["path"],
            feature["feature_revision"],
            Storage.sqlite_identity_value(feature["file_dev"]),
            Storage.sqlite_identity_value(feature["file_ino"]),
            feature["mtime_ns"],
            feature["file_size"],
            feature["width"],
            feature["height"],
            feature["pixel_digest"],
            feature["phash"],
            feature["phash_int"],
            feature["dhash"],
            feature["whash"],
            json.dumps(feature["avg_rgb"]),
            json.dumps(feature["color_hist"]),
            json.dumps(feature["hsv_hist"]),
            json.dumps(feature["luma_layout"]),
            json.dumps(feature["edge_layout"]),
            feature["quality_score"],
        ),
    )
    image_id = require_lastrowid(image_row)
    scan_row = conn.execute(
        """
        INSERT INTO scans (
          path, recursive, model, pretrained, device, thresholds_json, status,
          phase, progress_detail
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(tmp_path),
            1,
            "model",
            "pretrained",
            "cpu",
            "{}",
            "completed",
            "completed",
            "Completed with 0 duplicate group(s)",
        ),
    )
    scan_id = require_lastrowid(scan_row)
    conn.execute(
        """
        INSERT INTO scan_images (
          scan_id, image_id, path, feature_revision, file_dev, file_ino, mtime_ns,
          file_size, width, height, pixel_digest, phash, phash_int, dhash, whash,
          avg_rgb_json, color_hist_json, hsv_hist_json, luma_layout_json,
          edge_layout_json, quality_score, trashed
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            scan_id,
            image_id,
            feature["path"],
            feature["feature_revision"],
            Storage.sqlite_identity_value(feature["file_dev"]),
            Storage.sqlite_identity_value(feature["file_ino"]),
            feature["mtime_ns"],
            feature["file_size"],
            feature["width"],
            feature["height"],
            feature["pixel_digest"],
            feature["phash"],
            feature["phash_int"],
            feature["dhash"],
            feature["whash"],
            json.dumps(feature["avg_rgb"]),
            json.dumps(feature["color_hist"]),
            json.dumps(feature["hsv_hist"]),
            json.dumps(feature["luma_layout"]),
            json.dumps(feature["edge_layout"]),
            feature["quality_score"],
            0,
        ),
    )
    conn.commit()
    conn.close()

    storage = Storage(db_path)
    storage.init_schema()
    lightweight_id = storage.upsert_image(extract_lightweight_image_features(path))

    with storage.connect() as migrated:
        rows = migrated.execute(
            "SELECT id, feature_revision FROM images WHERE path=? ORDER BY id ASC",
            (feature["path"],),
        ).fetchall()
        snapshot = migrated.execute(
            "SELECT image_id FROM scan_images WHERE scan_id=? AND path=?",
            (scan_id, feature["path"]),
        ).fetchone()
        foreign_keys = migrated.execute("PRAGMA foreign_key_list(scan_images)").fetchall()
        foreign_key_errors = migrated.execute("PRAGMA foreign_key_check").fetchall()
    assert [row["feature_revision"] for row in rows] == [
        FEATURE_REVISION,
        LIGHTWEIGHT_FEATURE_REVISION,
    ]
    assert int(rows[1]["id"]) == lightweight_id
    assert snapshot is not None
    assert int(snapshot["image_id"]) == image_id
    assert {row["table"] for row in foreign_keys} >= {"images", "scans"}
    assert foreign_key_errors == []


def test_v4_database_adds_precision_feature_columns(tmp_path: Path) -> None:
    db_path = tmp_path / "v4.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        PRAGMA user_version=4;
        CREATE TABLE images (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          path TEXT NOT NULL UNIQUE,
          feature_revision TEXT NOT NULL,
          file_dev INTEGER NOT NULL DEFAULT 0,
          file_ino INTEGER NOT NULL DEFAULT 0,
          mtime_ns INTEGER NOT NULL,
          file_size INTEGER NOT NULL,
          width INTEGER NOT NULL,
          height INTEGER NOT NULL,
          pixel_digest TEXT NOT NULL,
          phash TEXT NOT NULL,
          phash_int TEXT NOT NULL,
          avg_rgb_json TEXT NOT NULL,
          color_hist_json TEXT NOT NULL,
          hsv_hist_json TEXT NOT NULL,
          clip_model TEXT,
          clip_pretrained TEXT,
          clip_revision TEXT,
          embedding_blob BLOB,
          embedding_json TEXT,
          trashed INTEGER NOT NULL DEFAULT 0,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE scan_images (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          scan_id INTEGER NOT NULL,
          image_id INTEGER,
          path TEXT NOT NULL,
          feature_revision TEXT NOT NULL,
          file_dev INTEGER NOT NULL,
          file_ino INTEGER NOT NULL,
          mtime_ns INTEGER NOT NULL,
          file_size INTEGER NOT NULL,
          width INTEGER NOT NULL,
          height INTEGER NOT NULL,
          pixel_digest TEXT NOT NULL,
          phash TEXT NOT NULL,
          phash_int TEXT NOT NULL,
          avg_rgb_json TEXT NOT NULL,
          color_hist_json TEXT NOT NULL,
          hsv_hist_json TEXT NOT NULL,
          trashed INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(scan_id, path)
        );
        CREATE TABLE group_members (
          group_id INTEGER NOT NULL,
          image_id INTEGER NOT NULL,
          score REAL NOT NULL,
          reason TEXT NOT NULL,
          PRIMARY KEY (group_id, image_id)
        );
        """
    )
    conn.commit()
    conn.close()

    storage = Storage(db_path)
    storage.init_schema()

    with storage.connect() as migrated:
        image_columns = {row["name"] for row in migrated.execute("PRAGMA table_info(images)")}
        snapshot_columns = {
            row["name"] for row in migrated.execute("PRAGMA table_info(scan_images)")
        }
        member_columns = {
            row["name"] for row in migrated.execute("PRAGMA table_info(group_members)")
        }
        expected = {"dhash", "whash", "luma_layout_json", "edge_layout_json", "quality_score"}
        assert expected.issubset(image_columns)
        assert expected.issubset(snapshot_columns)
        assert "evidence_json" in member_columns
        assert migrated.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_v6_database_migrates_image_cache_to_revision_key(tmp_path: Path) -> None:
    db_path = tmp_path / "v6.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        PRAGMA user_version=6;
        CREATE TABLE images (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          path TEXT NOT NULL UNIQUE,
          feature_revision TEXT NOT NULL,
          file_dev INTEGER NOT NULL DEFAULT 0,
          file_ino INTEGER NOT NULL DEFAULT 0,
          mtime_ns INTEGER NOT NULL,
          file_size INTEGER NOT NULL,
          width INTEGER NOT NULL,
          height INTEGER NOT NULL,
          pixel_digest TEXT NOT NULL,
          phash TEXT NOT NULL,
          phash_int TEXT NOT NULL,
          dhash TEXT NOT NULL DEFAULT '',
          whash TEXT NOT NULL DEFAULT '',
          avg_rgb_json TEXT NOT NULL,
          color_hist_json TEXT NOT NULL,
          hsv_hist_json TEXT NOT NULL,
          luma_layout_json TEXT NOT NULL DEFAULT '[]',
          edge_layout_json TEXT NOT NULL DEFAULT '[]',
          quality_score REAL NOT NULL DEFAULT 0,
          clip_model TEXT,
          clip_pretrained TEXT,
          clip_revision TEXT,
          embedding_blob BLOB,
          embedding_json TEXT,
          trashed INTEGER NOT NULL DEFAULT 0,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO images (
          id, path, feature_revision, file_dev, file_ino, mtime_ns, file_size, width, height,
          pixel_digest, phash, phash_int, dhash, whash, avg_rgb_json, color_hist_json,
          hsv_hist_json, luma_layout_json, edge_layout_json, quality_score, trashed
        )
        VALUES (
          42, '/tmp/image.png', 'old-revision', 1, 2, 3, 4, 20, 20,
          'digest', '0', '0', '', '', '[0, 0, 0]', '[]',
          '[]', '[]', '[]', 0.25, 1
        );
        """
    )
    conn.commit()
    conn.close()
    image_path = tmp_path / "image.png"
    Image.new("RGB", (20, 20), (10, 20, 30)).save(image_path)
    feature = extract_image_features(image_path)
    feature["path"] = "/tmp/image.png"
    feature["feature_revision"] = FEATURE_REVISION

    storage = Storage(db_path)
    storage.init_schema()
    current_id = storage.upsert_image(feature)

    assert current_id != 42
    with storage.connect() as migrated:
        keys = unique_index_columns(migrated, "images")
        rows = migrated.execute(
            "SELECT id, feature_revision, trashed FROM images WHERE path=? ORDER BY id",
            ("/tmp/image.png",),
        ).fetchall()
        assert ("path", "feature_revision") in keys
        assert [row["id"] for row in rows] == [42, current_id]
        assert rows[0]["feature_revision"] == "old-revision"
        assert rows[0]["trashed"] == 1
        assert migrated.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_group_member_evidence_round_trips_from_snapshot(tmp_path: Path) -> None:
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    Image.new("RGB", (20, 20), (10, 20, 30)).save(first)
    Image.new("RGB", (20, 20), (10, 20, 30)).save(second)
    storage = Storage(tmp_path / "db.sqlite3")
    storage.init_schema()
    scan_id = storage.create_scan(str(tmp_path), True, "model", "pretrained", "cpu", {})
    first_feature = extract_image_features(first)
    second_feature = extract_image_features(second)
    _, first_snapshot = storage.save_scan_image(scan_id, first_feature)
    _, second_snapshot = storage.save_scan_image(scan_id, second_feature)

    storage.create_groups(
        scan_id,
        [
            {
                "score": 0.98,
                "reason": "visual-hash",
                "recommended_keep_id": first_snapshot,
                "members": [
                    {"image_id": first_snapshot, "score": 1.0, "reason": "recommended-keep"},
                    {
                        "image_id": second_snapshot,
                        "score": 0.98,
                        "reason": "visual-hash",
                        "evidence": {"verified": True, "layout_similarity": 0.99},
                    },
                ],
            }
        ],
    )

    groups = storage.get_groups(scan_id, 10, 0)

    member = next(image for image in groups[0]["images"] if image["id"] == second_snapshot)
    assert member["evidence"] == {"verified": True, "layout_similarity": 0.99}


def test_group_persistence_rolls_back_replacement_when_cancelled(tmp_path: Path) -> None:
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    Image.new("RGB", (20, 20), (10, 20, 30)).save(first)
    Image.new("RGB", (20, 20), (20, 30, 40)).save(second)
    storage = Storage(tmp_path / "db.sqlite3")
    storage.init_schema()
    scan_id = storage.create_scan(str(tmp_path), True, "model", "pretrained", "cpu", {})
    _, first_snapshot = storage.save_scan_image(scan_id, extract_image_features(first))
    _, second_snapshot = storage.save_scan_image(scan_id, extract_image_features(second))
    original_group = {
        "score": 1.0,
        "reason": "exact-pixel",
        "recommended_keep_id": first_snapshot,
        "members": [
            {"image_id": first_snapshot, "score": 1.0, "reason": "recommended-keep"},
            {"image_id": second_snapshot, "score": 1.0, "reason": "exact-pixel"},
        ],
    }
    storage.create_groups(scan_id, [original_group])
    checks = 0

    def cancel_during_replacement() -> None:
        nonlocal checks
        checks += 1
        if checks == 3:
            raise RuntimeError("cancelled")

    replacement_group = {**original_group, "score": 0.8, "reason": "visual-hash"}
    with pytest.raises(RuntimeError, match="cancelled"):
        storage.create_groups(scan_id, [replacement_group], cancel_during_replacement)

    groups = storage.get_groups(scan_id, 10, 0)
    assert len(groups) == 1
    assert groups[0]["reason"] == "exact-pixel"
    assert len(groups[0]["images"]) == 2


def test_deleting_scan_prunes_unreferenced_feature_cache(tmp_path: Path) -> None:
    path = tmp_path / "image.png"
    Image.new("RGB", (20, 20), (10, 20, 30)).save(path)
    storage = Storage(tmp_path / "db.sqlite3")
    storage.init_schema()
    scan_id = storage.create_scan(str(tmp_path), True, "model", "pretrained", "cpu", {})
    feature = extract_image_features(path)
    cache_id, _ = storage.save_scan_image(scan_id, feature)
    storage.update_scan(scan_id, status="completed")

    assert storage.delete_scan(scan_id)
    assert storage.get_image(cache_id) is None


def test_deleting_scan_rolls_back_if_cache_pruning_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "image.png"
    Image.new("RGB", (20, 20), (10, 20, 30)).save(path)
    storage = Storage(tmp_path / "db.sqlite3")
    storage.init_schema()
    scan_id = storage.create_scan(str(tmp_path), True, "model", "pretrained", "cpu", {})
    cache_id, snapshot_id = storage.save_scan_image(scan_id, extract_image_features(path))
    storage.update_scan(scan_id, status="completed")

    def fail_prune(_conn: sqlite3.Connection) -> int:
        raise sqlite3.OperationalError("prune failed")

    monkeypatch.setattr(storage, "_prune_unreferenced_cache_in_connection", fail_prune)

    with pytest.raises(sqlite3.OperationalError, match="prune failed"):
        storage.delete_scan(scan_id)

    assert storage.get_scan(scan_id) is not None
    assert storage.get_scan_image(snapshot_id) is not None
    assert storage.get_image(cache_id) is not None


def test_recover_interrupted_scan_clears_incomplete_groups(tmp_path: Path) -> None:
    path = tmp_path / "image.png"
    Image.new("RGB", (20, 20), (10, 20, 30)).save(path)
    storage = Storage(tmp_path / "db.sqlite3")
    storage.init_schema()
    scan_id = storage.create_scan(str(tmp_path), True, "model", "pretrained", "cpu", {})
    feature = extract_image_features(path)
    _, snapshot_id = storage.save_scan_image(scan_id, feature)
    storage.create_groups(
        scan_id,
        [
            {
                "score": 1.0,
                "reason": "exact-pixel",
                "recommended_keep_id": snapshot_id,
                "members": [
                    {"image_id": snapshot_id, "score": 1.0, "reason": "recommended-keep"}
                ],
            }
        ],
    )

    assert storage.recover_interrupted_scans() == 1
    scan = require_row(storage.get_scan(scan_id))
    assert scan["status"] == "failed"
    assert scan["duplicate_groups"] == 0
    assert storage.count_groups(scan_id) == 0

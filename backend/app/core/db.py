from __future__ import annotations

import json
import sqlite3
import threading
from array import array
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Protocol, cast

from app.core.config import get_settings


SCHEMA_VERSION = 7
LEGACY_MIGRATION_WARNING = (
    "Legacy duplicate groups were cleared during the database upgrade. Rescan to restore results."
)
SQLiteValue = str | int | float | bytes | None
SQLiteParams = dict[str, SQLiteValue]
JsonDict = dict[str, Any]
SQLITE_MIN_INT = -(2**63)
SQLITE_MAX_INT = (2**63) - 1
SQLITE_BIG_INT_PREFIX = "bigint:"


class FileIdentityLike(Protocol):
    path: str
    file_dev: int
    file_ino: int
    mtime_ns: int
    file_size: int


class Storage:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._lock = threading.RLock()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=5.0)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._restrict_db_permissions()
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        finally:
            conn.close()
            self._restrict_db_permissions()

    def init_schema(self) -> None:
        with self._lock, self.connect() as conn:
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if version > SCHEMA_VERSION:
                raise RuntimeError(
                    f"Database schema version {version} is newer than supported version {SCHEMA_VERSION}"
                )
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute("BEGIN IMMEDIATE")
            if self._table_exists(conn, "scans") and not self._table_exists(conn, "scan_images"):
                self._migrate_legacy_schema(conn)
            elif self._table_exists(conn, "scan_images") and version < 3:
                self._migrate_v2_schema(conn)
            if self._table_exists(conn, "scans") and version < 4:
                self._migrate_v3_schema(conn)
            if (
                version < 5
                and (
                    self._table_exists(conn, "images")
                    or self._table_exists(conn, "scan_images")
                    or self._table_exists(conn, "group_members")
                )
            ):
                self._migrate_v4_schema(conn)
            if version < 7:
                self._migrate_v6_schema(conn)
            self._create_current_schema(conn)
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            conn.execute("PRAGMA foreign_keys=ON")

    def create_scan(
        self,
        path: str,
        recursive: bool,
        model: str,
        pretrained: str,
        device: str,
        thresholds: JsonDict,
        warnings: list[str] | None = None,
    ) -> int:
        with self._lock, self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO scans (
                  path, recursive, model, pretrained, device, thresholds_json,
                  status, warnings_json, phase, progress_detail
                )
                VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, 'queued', 'Waiting to start')
                """,
                (
                    path,
                    int(recursive),
                    model,
                    pretrained,
                    device,
                    json.dumps(thresholds),
                    json.dumps(warnings or []),
                ),
            )
            return self._last_insert_id(cur)

    def update_scan(self, scan_id: int, **values: Any) -> None:
        if not values:
            return
        allowed = {
            "status",
            "progress",
            "total_files",
            "processed_files",
            "succeeded_files",
            "failed_files",
            "skipped_files",
            "candidate_pairs",
            "duplicate_groups",
            "error",
            "warnings_json",
            "cancel_requested",
            "phase",
            "progress_detail",
        }
        keys = [key for key in values if key in allowed]
        if not keys:
            return
        assignments = ", ".join(f"{key}=?" for key in keys)
        params = [values[key] for key in keys]
        params.append(scan_id)
        with self._lock, self.connect() as conn:
            conn.execute(
                f"UPDATE scans SET {assignments}, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                params,
            )

    def add_scan_warning(self, scan_id: int, warning: str) -> None:
        with self._lock, self.connect() as conn:
            row = conn.execute("SELECT warnings_json FROM scans WHERE id=?", (scan_id,)).fetchone()
            if not row:
                return
            warnings = json.loads(row["warnings_json"] or "[]")
            if warning not in warnings:
                warnings.append(warning)
                conn.execute(
                    """
                    UPDATE scans
                    SET warnings_json=?, updated_at=CURRENT_TIMESTAMP
                    WHERE id=?
                    """,
                    (json.dumps(warnings), scan_id),
                )

    def get_scan(self, scan_id: int) -> JsonDict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM scans WHERE id=?", (scan_id,)).fetchone()
            return self._decode_scan_row(row) if row else None

    def list_scans(
        self,
        limit: int,
        offset: int,
        status: str | None = None,
    ) -> tuple[list[JsonDict], int]:
        with self.connect() as conn:
            # Keep the count and page on the same WAL read snapshot. Without an
            # explicit transaction a concurrent insert can make them disagree.
            conn.execute("BEGIN")
            if status is None:
                total = int(conn.execute("SELECT COUNT(*) FROM scans").fetchone()[0])
                rows = conn.execute(
                    """
                    SELECT * FROM scans
                    ORDER BY id DESC
                    LIMIT ? OFFSET ?
                    """,
                    (limit, offset),
                ).fetchall()
            else:
                total = int(
                    conn.execute(
                        "SELECT COUNT(*) FROM scans WHERE status=?",
                        (status,),
                    ).fetchone()[0]
                )
                rows = conn.execute(
                    """
                    SELECT * FROM scans
                    WHERE status=?
                    ORDER BY id DESC
                    LIMIT ? OFFSET ?
                    """,
                    (status, limit, offset),
                ).fetchall()
            return [self._decode_scan_row(row) for row in rows], total

    def request_cancel(self, scan_id: int) -> bool:
        with self._lock, self.connect() as conn:
            cur = conn.execute(
                """
                UPDATE scans
                SET cancel_requested=1,
                    progress_detail='Cancellation requested',
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND status IN ('queued', 'running')
                """,
                (scan_id,),
            )
            return cur.rowcount > 0

    def is_cancel_requested(self, scan_id: int) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT cancel_requested FROM scans WHERE id=?", (scan_id,)
            ).fetchone()
            return bool(row and row["cancel_requested"])

    def recover_interrupted_scans(self) -> int:
        with self._lock, self.connect() as conn:
            conn.execute(
                """
                DELETE FROM duplicate_groups
                WHERE scan_id IN (
                  SELECT id FROM scans WHERE status IN ('queued', 'running')
                )
                """
            )
            cur = conn.execute(
                """
                UPDATE scans
                SET status='failed',
                    error='Scan was interrupted by a previous application shutdown.',
                    phase='failed',
                    progress_detail='Scan was interrupted by a previous application shutdown.',
                    progress=1.0,
                    duplicate_groups=0,
                    cancel_requested=0,
                    updated_at=CURRENT_TIMESTAMP
                WHERE status IN ('queued', 'running')
                """
            )
            return cur.rowcount

    def delete_scan(self, scan_id: int) -> bool:
        with self._lock, self.connect() as conn:
            row = conn.execute("SELECT status FROM scans WHERE id=?", (scan_id,)).fetchone()
            if not row:
                return False
            if row["status"] in {"queued", "running"}:
                raise ValueError("Running scans must be cancelled before deletion")
            conn.execute("DELETE FROM scans WHERE id=?", (scan_id,))
            self._prune_unreferenced_cache_in_connection(conn)
        return True

    def reset_app_data(self) -> dict[str, int]:
        with self._lock, self.connect() as conn:
            active_count = int(
                conn.execute(
                    "SELECT COUNT(*) FROM scans WHERE status IN ('queued', 'running')"
                ).fetchone()[0]
            )
            if active_count:
                raise ValueError("Running scans must be cancelled before resetting app data")

            counts = {
                "scans_deleted": self._table_count(conn, "scans"),
                "scan_images_deleted": self._table_count(conn, "scan_images"),
                "groups_deleted": self._table_count(conn, "duplicate_groups"),
                "group_members_deleted": self._table_count(conn, "group_members"),
                "failures_deleted": self._table_count(conn, "scan_failures"),
                "cached_images_deleted": self._table_count(conn, "images"),
            }
            for table in (
                "group_members",
                "duplicate_groups",
                "scan_failures",
                "scan_images",
                "scans",
                "images",
            ):
                conn.execute(f"DELETE FROM {table}")
            if self._table_exists(conn, "sqlite_sequence"):
                conn.execute(
                    """
                    DELETE FROM sqlite_sequence
                    WHERE name IN (
                      'scans',
                      'images',
                      'scan_images',
                      'duplicate_groups',
                      'scan_failures'
                    )
                    """
                )
            return counts

    def get_cached_image(
        self, path: str, identity: Any, feature_revision: str
    ) -> JsonDict | None:
        cached = self.get_cached_images([identity], feature_revision)
        return cached.get(path) or cached.get(self._identity_path(identity))

    def get_cached_images(
        self,
        identities: list[Any],
        feature_revision: str,
        check_cancelled: Callable[[], None] | None = None,
    ) -> dict[str, JsonDict]:
        if not identities:
            return {}
        cached: dict[str, JsonDict] = {}
        requested: list[tuple[SQLiteValue, ...]] = []
        seen: set[tuple[SQLiteValue, ...]] = set()
        for identity in identities:
            values = self._identity_values(identity)
            lookup = (
                self._identity_path(identity),
                values["file_dev"],
                values["file_ino"],
                values["mtime_ns"],
                values["file_size"],
            )
            if lookup not in seen:
                requested.append(lookup)
                seen.add(lookup)

        trashed_ids: list[int] = []
        with self._lock, self.connect() as conn:
            for index in range(0, len(requested), 100):
                if check_cancelled:
                    check_cancelled()
                chunk = requested[index : index + 100]
                value_rows = ",".join("(?,?,?,?,?)" for _ in chunk)
                params = [value for lookup in chunk for value in lookup]
                rows = conn.execute(
                    f"""
                    WITH requested(path, file_dev, file_ino, mtime_ns, file_size) AS (
                      VALUES {value_rows}
                    )
                    SELECT images.*
                    FROM images
                    JOIN requested
                      ON requested.path=images.path
                     AND requested.file_dev=images.file_dev
                     AND requested.file_ino=images.file_ino
                     AND requested.mtime_ns=images.mtime_ns
                     AND requested.file_size=images.file_size
                    WHERE images.feature_revision=?
                    """,
                    (*params, feature_revision),
                ).fetchall()
                for row in rows:
                    if row["trashed"]:
                        trashed_ids.append(int(row["id"]))
                    decoded = self._decode_image_row(row)
                    decoded["trashed"] = False
                    cached[str(decoded["path"])] = decoded
            if check_cancelled:
                check_cancelled()
            for chunk in self._chunks(trashed_ids, 500):
                placeholders = ",".join("?" for _ in chunk)
                conn.execute(
                    f"""
                    UPDATE images
                    SET trashed=0, updated_at=CURRENT_TIMESTAMP
                    WHERE id IN ({placeholders})
                    """,
                    chunk,
                )
            if check_cancelled:
                check_cancelled()
        return cached

    def upsert_image(self, feature: JsonDict) -> int:
        with self._lock, self.connect() as conn:
            return self._upsert_image(conn, feature)

    def get_image(self, image_id: int) -> JsonDict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM images WHERE id=?", (image_id,)).fetchone()
            return self._decode_image_row(row) if row else None

    def save_scan_image(self, scan_id: int, feature: JsonDict) -> tuple[int, int]:
        with self._lock:
            return self.save_scan_images_bulk(scan_id, [feature])[0]

    def save_scan_images_bulk(
        self,
        scan_id: int,
        features: list[JsonDict],
        check_cancelled: Callable[[], None] | None = None,
    ) -> list[tuple[int, int]]:
        if not features:
            return []
        saved: list[tuple[int, int]] = []
        with self._lock, self.connect() as conn:
            for feature in features:
                if check_cancelled:
                    check_cancelled()
                image_id = self._upsert_image(conn, feature)
                scan_image_id = self._create_scan_image(conn, scan_id, image_id, feature)
                saved.append((image_id, scan_image_id))
            if check_cancelled:
                check_cancelled()
        return saved

    def set_embedding(
        self,
        image_id: int,
        model: str,
        pretrained: str,
        revision: str,
        embedding: list[float],
    ) -> None:
        self.set_embeddings_bulk(
            model,
            pretrained,
            revision,
            [(image_id, embedding)],
        )

    def get_clip_embeddings(
        self,
        image_ids: list[int],
        model: str,
        pretrained: str,
        revision: str,
    ) -> dict[int, list[float]]:
        if not image_ids:
            return {}
        embeddings: dict[int, list[float]] = {}
        unique_ids = sorted(set(image_ids))
        with self.connect() as conn:
            for chunk in self._chunks(unique_ids, 500):
                placeholders = ",".join("?" for _ in chunk)
                rows = conn.execute(
                    f"""
                    SELECT id, embedding_blob, embedding_json
                    FROM images
                    WHERE id IN ({placeholders})
                      AND clip_model=?
                      AND clip_pretrained=?
                      AND clip_revision=?
                      AND (embedding_blob IS NOT NULL OR embedding_json IS NOT NULL)
                    """,
                    (*chunk, model, pretrained, revision),
                ).fetchall()
                for row in rows:
                    embedding = self._decode_embedding(
                        row["embedding_blob"],
                        row["embedding_json"],
                    )
                    if embedding:
                        embeddings[int(row["id"])] = embedding
        return embeddings

    def set_embeddings_bulk(
        self,
        model: str,
        pretrained: str,
        revision: str,
        embeddings: list[tuple[int, list[float]]],
        check_cancelled: Callable[[], None] | None = None,
    ) -> None:
        if not embeddings:
            return
        params = [
            (
                model,
                pretrained,
                revision,
                array("f", embedding).tobytes(),
                image_id,
            )
            for image_id, embedding in embeddings
        ]
        with self._lock, self.connect() as conn:
            if check_cancelled:
                check_cancelled()
            conn.executemany(
                """
                UPDATE images
                SET clip_model=?,
                    clip_pretrained=?,
                    clip_revision=?,
                    embedding_blob=?,
                    embedding_json=NULL,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                params,
            )
            if check_cancelled:
                check_cancelled()

    def create_scan_image(self, scan_id: int, image_id: int, feature: JsonDict) -> int:
        with self._lock, self.connect() as conn:
            return self._create_scan_image(conn, scan_id, image_id, feature)

    def get_scan_image(self, scan_image_id: int) -> JsonDict | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM scan_images WHERE id=?", (scan_image_id,)
            ).fetchone()
            return self._decode_image_row(row) if row else None

    def create_groups(
        self,
        scan_id: int,
        groups: list[JsonDict],
        check_cancelled: Callable[[], None] | None = None,
    ) -> None:
        with self._lock, self.connect() as conn:
            if check_cancelled:
                check_cancelled()
            conn.execute("DELETE FROM duplicate_groups WHERE scan_id=?", (scan_id,))
            for group in groups:
                if check_cancelled:
                    check_cancelled()
                cur = conn.execute(
                    """
                    INSERT INTO duplicate_groups
                      (scan_id, score, reason, recommended_keep_id)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        scan_id,
                        float(group["score"]),
                        group["reason"],
                        int(group["recommended_keep_id"]),
                    ),
                )
                group_id = self._last_insert_id(cur)
                for member in group["members"]:
                    if check_cancelled:
                        check_cancelled()
                    conn.execute(
                        """
                        INSERT INTO group_members (group_id, image_id, score, reason, evidence_json)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            group_id,
                            int(member["image_id"]),
                            float(member["score"]),
                            member["reason"],
                            json.dumps(member.get("evidence") or {}),
                        ),
                    )
            if check_cancelled:
                check_cancelled()

    def count_groups(self, scan_id: int) -> int:
        with self.connect() as conn:
            return int(
                conn.execute(
                    "SELECT COUNT(*) FROM duplicate_groups WHERE scan_id=?", (scan_id,)
                ).fetchone()[0]
            )

    def count_group_summaries(self, scan_id: int, group_filter: str) -> int:
        where, params = self._group_summary_filter(scan_id, group_filter)
        with self.connect() as conn:
            return int(
                conn.execute(
                    f"SELECT COUNT(*) FROM duplicate_groups dg WHERE {where}",
                    params,
                ).fetchone()[0]
            )

    def get_group_summaries(
        self,
        scan_id: int,
        limit: int,
        offset: int,
        group_filter: str,
        sort: str,
    ) -> list[JsonDict]:
        with self.connect() as conn:
            return self._get_group_summaries_in_connection(
                conn,
                scan_id,
                limit,
                offset,
                group_filter,
                sort,
            )

    def list_group_summaries_page(
        self,
        scan_id: int,
        limit: int,
        offset: int,
        group_filter: str,
        sort: str,
    ) -> tuple[list[JsonDict], int]:
        where, params = self._group_summary_filter(scan_id, group_filter)
        with self.connect() as conn:
            conn.execute("BEGIN")
            total = int(
                conn.execute(
                    f"SELECT COUNT(*) FROM duplicate_groups dg WHERE {where}",
                    params,
                ).fetchone()[0]
            )
            groups = self._get_group_summaries_in_connection(
                conn,
                scan_id,
                limit,
                offset,
                group_filter,
                sort,
            )
            return groups, total

    def _get_group_summaries_in_connection(
        self,
        conn: sqlite3.Connection,
        scan_id: int,
        limit: int,
        offset: int,
        group_filter: str,
        sort: str,
    ) -> list[JsonDict]:
        where, params = self._group_summary_filter(scan_id, group_filter)
        order_by = self._group_summary_order(sort)
        rows = conn.execute(
            f"""
            SELECT
              dg.id,
              dg.score,
              dg.reason,
              dg.recommended_keep_id,
              keep.path AS recommended_keep_path,
              COUNT(gm.image_id) AS image_count,
              COALESCE(
                SUM(CASE WHEN COALESCE(member.trashed, 0)=1 THEN 1 ELSE 0 END),
                0
              ) AS moved_count
            FROM duplicate_groups dg
            JOIN scan_images keep ON keep.id=dg.recommended_keep_id
            LEFT JOIN group_members gm ON gm.group_id=dg.id
            LEFT JOIN scan_images member ON member.id=gm.image_id
            WHERE {where}
            GROUP BY dg.id
            ORDER BY {order_by}
            LIMIT ? OFFSET ?
            """,
            (*params, limit, offset),
        ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "score": float(row["score"]),
                "reason": row["reason"],
                "recommended_keep_id": int(row["recommended_keep_id"]),
                "recommended_keep_path": row["recommended_keep_path"],
                "image_count": int(row["image_count"]),
                "moved_count": int(row["moved_count"]),
            }
            for row in rows
        ]

    def get_groups(self, scan_id: int, limit: int, offset: int) -> list[JsonDict]:
        with self.connect() as conn:
            return self._get_groups_in_connection(conn, scan_id, limit, offset)

    def list_groups_page(
        self,
        scan_id: int,
        limit: int,
        offset: int,
    ) -> tuple[list[JsonDict], int]:
        with self.connect() as conn:
            conn.execute("BEGIN")
            total = int(
                conn.execute(
                    "SELECT COUNT(*) FROM duplicate_groups WHERE scan_id=?",
                    (scan_id,),
                ).fetchone()[0]
            )
            groups = self._get_groups_in_connection(conn, scan_id, limit, offset)
            return groups, total

    def _get_groups_in_connection(
        self,
        conn: sqlite3.Connection,
        scan_id: int,
        limit: int,
        offset: int,
    ) -> list[JsonDict]:
        group_rows = conn.execute(
            """
            SELECT * FROM duplicate_groups
            WHERE scan_id=?
            ORDER BY score DESC, id ASC
            LIMIT ? OFFSET ?
            """,
            (scan_id, limit, offset),
        ).fetchall()
        return self._load_group_members(conn, group_rows)

    def get_group(self, scan_id: int, group_id: int) -> JsonDict | None:
        with self.connect() as conn:
            group_rows = conn.execute(
                """
                SELECT * FROM duplicate_groups
                WHERE scan_id=? AND id=?
                """,
                (scan_id, group_id),
            ).fetchall()
            groups = self._load_group_members(conn, group_rows)
            return groups[0] if groups else None

    def mark_trashed(self, scan_image_ids: list[int]) -> list[int]:
        if not scan_image_ids:
            return []
        with self._lock, self.connect() as conn:
            return self._mark_trashed_in_connection(conn, scan_image_ids)

    def _mark_trashed_in_connection(
        self,
        conn: sqlite3.Connection,
        scan_image_ids: list[int],
    ) -> list[int]:
        placeholders = ",".join("?" for _ in scan_image_ids)
        affected_scan_ids: set[int] = set()
        rows = conn.execute(
            f"""
            SELECT path, file_dev, file_ino, mtime_ns, file_size, pixel_digest
            FROM scan_images
            WHERE id IN ({placeholders})
            """,
            scan_image_ids,
        ).fetchall()
        for row in rows:
            identity = (
                row["path"],
                row["file_dev"],
                row["file_ino"],
                row["mtime_ns"],
                row["file_size"],
                row["pixel_digest"],
            )
            affected_rows = conn.execute(
                """
                SELECT DISTINCT scan_id
                FROM scan_images
                WHERE path=?
                  AND file_dev=?
                  AND file_ino=?
                  AND mtime_ns=?
                  AND file_size=?
                  AND pixel_digest=?
                """,
                identity,
            ).fetchall()
            affected_scan_ids.update(int(affected_row["scan_id"]) for affected_row in affected_rows)
            conn.execute(
                """
                UPDATE scan_images
                SET trashed=1
                WHERE path=?
                  AND file_dev=?
                  AND file_ino=?
                  AND mtime_ns=?
                  AND file_size=?
                  AND pixel_digest=?
                """,
                identity,
            )
            conn.execute(
                """
                UPDATE images
                SET trashed=1, updated_at=CURRENT_TIMESTAMP
                WHERE path=?
                  AND file_dev=?
                  AND file_ino=?
                  AND mtime_ns=?
                  AND file_size=?
                  AND pixel_digest=?
                """,
                identity,
            )
        return sorted(affected_scan_ids)

    def mark_moved(self, scan_image_ids: list[int]) -> list[int]:
        return self.mark_trashed(scan_image_ids)

    def mark_moved_and_prune(self, scan_image_ids: list[int]) -> list[int]:
        if not scan_image_ids:
            return []
        with self._lock, self.connect() as conn:
            affected_scan_ids = self._mark_trashed_in_connection(conn, scan_image_ids)
            for scan_id in affected_scan_ids:
                self._prune_resolved_groups_in_connection(conn, scan_id)
            return affected_scan_ids

    def prune_resolved_groups(self, scan_id: int) -> list[int]:
        with self._lock, self.connect() as conn:
            return self._prune_resolved_groups_in_connection(conn, scan_id)

    def _prune_resolved_groups_in_connection(
        self,
        conn: sqlite3.Connection,
        scan_id: int,
    ) -> list[int]:
        rows = conn.execute(
            """
            SELECT dg.id
            FROM duplicate_groups dg
            LEFT JOIN group_members gm ON gm.group_id=dg.id
            LEFT JOIN scan_images si ON si.id=gm.image_id
            WHERE dg.scan_id=?
            GROUP BY dg.id
            HAVING COALESCE(SUM(CASE WHEN COALESCE(si.trashed, 0)=0 THEN 1 ELSE 0 END), 0) < 2
            """,
            (scan_id,),
        ).fetchall()
        group_ids = [int(row["id"]) for row in rows]
        if not group_ids:
            return []
        for chunk in self._chunks(group_ids, 500):
            placeholders = ",".join("?" for _ in chunk)
            conn.execute(
                f"""
                DELETE FROM duplicate_groups
                WHERE scan_id=? AND id IN ({placeholders})
                """,
                (scan_id, *chunk),
            )
        conn.execute(
            """
            UPDATE scans
            SET duplicate_groups=(
                SELECT COUNT(*) FROM duplicate_groups WHERE scan_id=?
            ),
                progress_detail='Completed with '
                  || (SELECT COUNT(*) FROM duplicate_groups WHERE scan_id=?)
                  || ' duplicate group(s)',
                updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (scan_id, scan_id, scan_id),
        )
        return group_ids

    def record_failure(self, scan_id: int, path: str, stage: str, error: str) -> None:
        self.record_failures_bulk(scan_id, [(path, stage, error)])

    def record_failures_bulk(
        self, scan_id: int, failures: list[tuple[str, str, str]]
    ) -> None:
        if not failures:
            return
        with self._lock, self.connect() as conn:
            conn.executemany(
                """
                INSERT INTO scan_failures (scan_id, path, stage, error)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (scan_id, path, stage, error[:4000])
                    for path, stage, error in failures
                ],
            )

    def list_failures(
        self, scan_id: int, limit: int, offset: int
    ) -> tuple[list[JsonDict], int]:
        with self.connect() as conn:
            conn.execute("BEGIN")
            total = int(
                conn.execute(
                    "SELECT COUNT(*) FROM scan_failures WHERE scan_id=?", (scan_id,)
                ).fetchone()[0]
            )
            rows = conn.execute(
                """
                SELECT * FROM scan_failures
                WHERE scan_id=?
                ORDER BY id ASC
                LIMIT ? OFFSET ?
                """,
                (scan_id, limit, offset),
            ).fetchall()
            return [self._row_dict(row) for row in rows], total

    def prune_unreferenced_cache(self) -> int:
        with self._lock, self.connect() as conn:
            return self._prune_unreferenced_cache_in_connection(conn)

    @staticmethod
    def _prune_unreferenced_cache_in_connection(conn: sqlite3.Connection) -> int:
        cur = conn.execute(
            """
            DELETE FROM images
            WHERE id NOT IN (
              SELECT DISTINCT image_id FROM scan_images WHERE image_id IS NOT NULL
            )
            """
        )
        return cur.rowcount

    def _load_group_members(
        self, conn: sqlite3.Connection, group_rows: list[sqlite3.Row]
    ) -> list[JsonDict]:
        if not group_rows:
            return []
        group_ids = [int(row["id"]) for row in group_rows]
        placeholders = ",".join("?" for _ in group_ids)
        member_rows = conn.execute(
            f"""
            SELECT
              gm.group_id,
              gm.score AS member_score,
              gm.reason AS member_reason,
              gm.evidence_json AS member_evidence_json,
              si.*
            FROM group_members gm
            JOIN scan_images si ON si.id = gm.image_id
            WHERE gm.group_id IN ({placeholders})
            ORDER BY gm.group_id ASC, (si.width * si.height) DESC, si.file_size DESC, si.path ASC
            """,
            group_ids,
        ).fetchall()
        members_by_group: dict[int, list[JsonDict]] = {group_id: [] for group_id in group_ids}
        for row in member_rows:
            decoded = self._decode_image_row(row, member_score=float(row["member_score"]))
            decoded["reason"] = row["member_reason"]
            evidence = json.loads(row["member_evidence_json"] or "{}")
            decoded["evidence"] = evidence if isinstance(evidence, dict) else {}
            members_by_group[int(row["group_id"])].append(decoded)
        return [
            {
                "id": int(group_row["id"]),
                "score": float(group_row["score"]),
                "reason": group_row["reason"],
                "recommended_keep_id": int(group_row["recommended_keep_id"]),
                "images": members_by_group[int(group_row["id"])],
            }
            for group_row in group_rows
        ]

    @staticmethod
    def _row_dict(row: sqlite3.Row) -> JsonDict:
        return dict(row)

    def _decode_scan_row(self, row: sqlite3.Row) -> JsonDict:
        return self._decode_scan(self._row_dict(row))

    def _decode_image_row(
        self, row: sqlite3.Row, member_score: float | None = None
    ) -> JsonDict:
        return self._decode_image(self._row_dict(row), member_score=member_score)

    def _decode_scan(self, row: JsonDict) -> JsonDict:
        row["warnings"] = json.loads(row.pop("warnings_json", None) or "[]")
        row["thresholds"] = json.loads(row.pop("thresholds_json", None) or "{}")
        row["recursive"] = bool(row["recursive"])
        row["cancel_requested"] = bool(row.get("cancel_requested", 0))
        return row

    def _decode_image(
        self, row: JsonDict, member_score: float | None = None
    ) -> JsonDict:
        row["avg_rgb"] = json.loads(row.pop("avg_rgb_json"))
        row["color_hist"] = json.loads(row.pop("color_hist_json"))
        row["hsv_hist"] = json.loads(row.pop("hsv_hist_json"))
        row["luma_layout"] = json.loads(row.pop("luma_layout_json", "[]") or "[]")
        row["edge_layout"] = json.loads(row.pop("edge_layout_json", "[]") or "[]")
        row["quality_score"] = float(row.get("quality_score", 0.0) or 0.0)
        row.pop("color_bucket_json", None)
        row["embedding"] = self._decode_embedding(
            row.pop("embedding_blob", None),
            row.pop("embedding_json", None),
        )
        for key in ("file_dev", "file_ino"):
            if key in row and row[key] is not None:
                row[key] = self._identity_int(row[key])
        for key in ("mtime_ns", "file_size", "width", "height"):
            if key in row and row[key] is not None:
                row[key] = int(row[key])
        row["score"] = 1.0 if member_score is None else member_score
        row["trashed"] = bool(row.get("trashed", 0))
        return row

    def _migrate_legacy_schema(self, conn: sqlite3.Connection) -> None:
        scan_columns = {
            "succeeded_files": "INTEGER NOT NULL DEFAULT 0",
            "failed_files": "INTEGER NOT NULL DEFAULT 0",
            "skipped_files": "INTEGER NOT NULL DEFAULT 0",
            "warnings_json": "TEXT NOT NULL DEFAULT '[]'",
            "cancel_requested": "INTEGER NOT NULL DEFAULT 0",
        }
        for name, definition in scan_columns.items():
            self._ensure_column(conn, "scans", name, definition)

        image_columns = {
            "feature_revision": "TEXT NOT NULL DEFAULT ''",
            "file_dev": "INTEGER NOT NULL DEFAULT 0",
            "file_ino": "INTEGER NOT NULL DEFAULT 0",
            "clip_revision": "TEXT",
            "embedding_blob": "BLOB",
        }
        for name, definition in image_columns.items():
            self._ensure_column(conn, "images", name, definition)

        conn.execute("DROP TABLE IF EXISTS group_members")
        conn.execute("DROP TABLE IF EXISTS duplicate_groups")
        conn.execute(
            """
            UPDATE scans
            SET duplicate_groups=0,
                warnings_json=?
            WHERE duplicate_groups > 0
            """,
            (json.dumps([LEGACY_MIGRATION_WARNING]),),
        )

    def _migrate_v2_schema(self, conn: sqlite3.Connection) -> None:
        self._ensure_column(conn, "images", "feature_revision", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column(conn, "scan_images", "feature_revision", "TEXT NOT NULL DEFAULT ''")

    def _migrate_v3_schema(self, conn: sqlite3.Connection) -> None:
        self._ensure_column(conn, "scans", "phase", "TEXT NOT NULL DEFAULT 'queued'")
        self._ensure_column(conn, "scans", "progress_detail", "TEXT NOT NULL DEFAULT ''")
        conn.execute(
            """
            UPDATE scans
            SET phase=CASE
                  WHEN status IN ('completed', 'failed', 'cancelled') THEN status
                  WHEN status='running' THEN 'extracting'
                  ELSE 'queued'
                END,
                progress_detail=CASE
                  WHEN status='completed' THEN 'Completed'
                  WHEN status='failed' THEN COALESCE(error, 'Scan failed')
                  WHEN status='cancelled' THEN 'Scan cancelled'
                  WHEN status='running' THEN 'Scan was running during migration'
                  ELSE COALESCE(NULLIF(progress_detail, ''), 'Waiting to start')
                END
            WHERE phase='queued' AND progress_detail=''
            """
        )

    def _migrate_v4_schema(self, conn: sqlite3.Connection) -> None:
        feature_columns = {
            "dhash": "TEXT NOT NULL DEFAULT ''",
            "whash": "TEXT NOT NULL DEFAULT ''",
            "luma_layout_json": "TEXT NOT NULL DEFAULT '[]'",
            "edge_layout_json": "TEXT NOT NULL DEFAULT '[]'",
            "quality_score": "REAL NOT NULL DEFAULT 0",
        }
        for table in ("images", "scan_images"):
            if self._table_exists(conn, table):
                for name, definition in feature_columns.items():
                    self._ensure_column(conn, table, name, definition)
        if self._table_exists(conn, "group_members"):
            self._ensure_column(
                conn,
                "group_members",
                "evidence_json",
                "TEXT NOT NULL DEFAULT '{}'",
            )

    def _migrate_v6_schema(self, conn: sqlite3.Connection) -> None:
        if not self._table_exists(conn, "images") or self._has_image_revision_unique_key(conn):
            return
        self._ensure_column(conn, "images", "feature_revision", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column(conn, "images", "file_dev", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column(conn, "images", "file_ino", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column(conn, "images", "dhash", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column(conn, "images", "whash", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column(conn, "images", "luma_layout_json", "TEXT NOT NULL DEFAULT '[]'")
        self._ensure_column(conn, "images", "edge_layout_json", "TEXT NOT NULL DEFAULT '[]'")
        self._ensure_column(conn, "images", "quality_score", "REAL NOT NULL DEFAULT 0")
        self._ensure_column(conn, "images", "clip_model", "TEXT")
        self._ensure_column(conn, "images", "clip_pretrained", "TEXT")
        self._ensure_column(conn, "images", "clip_revision", "TEXT")
        self._ensure_column(conn, "images", "embedding_blob", "BLOB")
        self._ensure_column(conn, "images", "embedding_json", "TEXT")
        self._ensure_column(conn, "images", "trashed", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column(conn, "images", "updated_at", "TEXT")
        conn.execute("DROP TABLE IF EXISTS images_v7_migration")
        conn.execute(
            """
            CREATE TABLE images_v7_migration (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              path TEXT NOT NULL,
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
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              UNIQUE(path, feature_revision)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO images_v7_migration (
              id, path, feature_revision, file_dev, file_ino, mtime_ns, file_size, width, height,
              pixel_digest, phash, phash_int, dhash, whash, avg_rgb_json, color_hist_json,
              hsv_hist_json, luma_layout_json, edge_layout_json, quality_score, clip_model,
              clip_pretrained, clip_revision, embedding_blob, embedding_json, trashed, updated_at
            )
            SELECT
              id, path, feature_revision, file_dev, file_ino, mtime_ns, file_size, width, height,
              pixel_digest, phash, phash_int, dhash, whash, avg_rgb_json, color_hist_json,
              hsv_hist_json, luma_layout_json, edge_layout_json, quality_score, clip_model,
              clip_pretrained, clip_revision, embedding_blob, embedding_json, trashed,
              COALESCE(updated_at, CURRENT_TIMESTAMP)
            FROM images
            ORDER BY id
            """
        )
        conn.execute("DROP TABLE images")
        conn.execute("ALTER TABLE images_v7_migration RENAME TO images")

    def _create_current_schema(self, conn: sqlite3.Connection) -> None:
        self._execute_schema_script(
            conn,
            """
            CREATE TABLE IF NOT EXISTS scans (
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

            CREATE TABLE IF NOT EXISTS images (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              path TEXT NOT NULL,
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
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              UNIQUE(path, feature_revision)
            );

            CREATE TABLE IF NOT EXISTS scan_images (
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

            CREATE TABLE IF NOT EXISTS duplicate_groups (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              scan_id INTEGER NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
              score REAL NOT NULL,
              reason TEXT NOT NULL,
              recommended_keep_id INTEGER NOT NULL REFERENCES scan_images(id) ON DELETE CASCADE,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS group_members (
              group_id INTEGER NOT NULL REFERENCES duplicate_groups(id) ON DELETE CASCADE,
              image_id INTEGER NOT NULL REFERENCES scan_images(id) ON DELETE CASCADE,
              score REAL NOT NULL,
              reason TEXT NOT NULL,
              evidence_json TEXT NOT NULL DEFAULT '{}',
              PRIMARY KEY (group_id, image_id)
            );

            CREATE TABLE IF NOT EXISTS scan_failures (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              scan_id INTEGER NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
              path TEXT NOT NULL,
              stage TEXT NOT NULL,
              error TEXT NOT NULL,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_images_digest ON images(pixel_digest);
            CREATE INDEX IF NOT EXISTS idx_scans_status_id ON scans(status, id DESC);
            CREATE INDEX IF NOT EXISTS idx_images_feature_identity
              ON images(path, feature_revision, file_dev, file_ino, mtime_ns, file_size);
            CREATE INDEX IF NOT EXISTS idx_scan_images_scan ON scan_images(scan_id);
            CREATE INDEX IF NOT EXISTS idx_scan_images_path ON scan_images(path);
            CREATE INDEX IF NOT EXISTS idx_groups_scan ON duplicate_groups(scan_id);
            CREATE INDEX IF NOT EXISTS idx_groups_scan_reason_score
              ON duplicate_groups(scan_id, reason, score DESC, id);
            CREATE INDEX IF NOT EXISTS idx_group_members_image ON group_members(image_id);
            CREATE INDEX IF NOT EXISTS idx_failures_scan ON scan_failures(scan_id);
            """,
        )

    @staticmethod
    def _execute_schema_script(conn: sqlite3.Connection, script: str) -> None:
        pending: list[str] = []
        for line in script.splitlines():
            pending.append(line)
            statement = "\n".join(pending).strip()
            if not statement or not sqlite3.complete_statement(statement):
                continue
            conn.execute(statement)
            pending.clear()
        if "\n".join(pending).strip():
            raise sqlite3.OperationalError("Incomplete schema statement")

    @staticmethod
    def _group_summary_filter(scan_id: int, group_filter: str) -> tuple[str, tuple[SQLiteValue, ...]]:
        if group_filter == "exact":
            return "dg.scan_id=? AND dg.reason='exact-pixel'", (scan_id,)
        if group_filter == "visual":
            return "dg.scan_id=? AND dg.reason<>'exact-pixel'", (scan_id,)
        return "dg.scan_id=?", (scan_id,)

    @staticmethod
    def _group_summary_order(sort: str) -> str:
        if sort == "files":
            return "image_count DESC, dg.score DESC, dg.id ASC"
        return "dg.score DESC, dg.id ASC"

    def _upsert_image(self, conn: sqlite3.Connection, feature: JsonDict) -> int:
        params = self._feature_params(feature)
        conn.execute(
            """
            INSERT INTO images (
              path, feature_revision, file_dev, file_ino, mtime_ns, file_size, width, height,
              pixel_digest, phash, phash_int, dhash, whash, avg_rgb_json, color_hist_json,
              hsv_hist_json, luma_layout_json, edge_layout_json, quality_score
            )
            VALUES (
              :path, :feature_revision, :file_dev, :file_ino, :mtime_ns, :file_size, :width, :height,
              :pixel_digest, :phash, :phash_int, :dhash, :whash, :avg_rgb_json, :color_hist_json,
              :hsv_hist_json, :luma_layout_json, :edge_layout_json, :quality_score
            )
            ON CONFLICT(path, feature_revision) DO UPDATE SET
              file_dev=excluded.file_dev,
              file_ino=excluded.file_ino,
              mtime_ns=excluded.mtime_ns,
              file_size=excluded.file_size,
              width=excluded.width,
              height=excluded.height,
              pixel_digest=excluded.pixel_digest,
              phash=excluded.phash,
              phash_int=excluded.phash_int,
              dhash=excluded.dhash,
              whash=excluded.whash,
              avg_rgb_json=excluded.avg_rgb_json,
              color_hist_json=excluded.color_hist_json,
              hsv_hist_json=excluded.hsv_hist_json,
              luma_layout_json=excluded.luma_layout_json,
              edge_layout_json=excluded.edge_layout_json,
              quality_score=excluded.quality_score,
              clip_model=CASE
                WHEN images.feature_revision=excluded.feature_revision
                  AND images.file_dev=excluded.file_dev
                  AND images.file_ino=excluded.file_ino
                  AND images.mtime_ns=excluded.mtime_ns
                  AND images.file_size=excluded.file_size
                  AND images.pixel_digest=excluded.pixel_digest
                THEN images.clip_model ELSE NULL END,
              clip_pretrained=CASE
                WHEN images.feature_revision=excluded.feature_revision
                  AND images.file_dev=excluded.file_dev
                  AND images.file_ino=excluded.file_ino
                  AND images.mtime_ns=excluded.mtime_ns
                  AND images.file_size=excluded.file_size
                  AND images.pixel_digest=excluded.pixel_digest
                THEN images.clip_pretrained ELSE NULL END,
              clip_revision=CASE
                WHEN images.feature_revision=excluded.feature_revision
                  AND images.file_dev=excluded.file_dev
                  AND images.file_ino=excluded.file_ino
                  AND images.mtime_ns=excluded.mtime_ns
                  AND images.file_size=excluded.file_size
                  AND images.pixel_digest=excluded.pixel_digest
                THEN images.clip_revision ELSE NULL END,
              embedding_blob=CASE
                WHEN images.feature_revision=excluded.feature_revision
                  AND images.file_dev=excluded.file_dev
                  AND images.file_ino=excluded.file_ino
                  AND images.mtime_ns=excluded.mtime_ns
                  AND images.file_size=excluded.file_size
                  AND images.pixel_digest=excluded.pixel_digest
                THEN images.embedding_blob ELSE NULL END,
              embedding_json=CASE
                WHEN images.feature_revision=excluded.feature_revision
                  AND images.file_dev=excluded.file_dev
                  AND images.file_ino=excluded.file_ino
                  AND images.mtime_ns=excluded.mtime_ns
                  AND images.file_size=excluded.file_size
                  AND images.pixel_digest=excluded.pixel_digest
                THEN images.embedding_json ELSE NULL END,
              trashed=0,
              updated_at=CURRENT_TIMESTAMP
            """,
            params,
        )
        row = conn.execute(
            "SELECT id FROM images WHERE path=? AND feature_revision=?",
            (feature["path"], feature["feature_revision"]),
        ).fetchone()
        return int(row["id"])

    def _create_scan_image(
        self,
        conn: sqlite3.Connection,
        scan_id: int,
        image_id: int,
        feature: JsonDict,
    ) -> int:
        params: SQLiteParams = {
            "scan_id": scan_id,
            "image_id": image_id,
            **self._feature_params(feature),
        }
        cur = conn.execute(
            """
            INSERT INTO scan_images (
              scan_id, image_id, path, feature_revision, file_dev, file_ino, mtime_ns, file_size,
              width, height, pixel_digest, phash, phash_int, dhash, whash, avg_rgb_json,
              color_hist_json, hsv_hist_json, luma_layout_json, edge_layout_json, quality_score
            )
            VALUES (
              :scan_id, :image_id, :path, :feature_revision, :file_dev, :file_ino, :mtime_ns, :file_size,
              :width, :height, :pixel_digest, :phash, :phash_int, :dhash, :whash, :avg_rgb_json,
              :color_hist_json, :hsv_hist_json, :luma_layout_json, :edge_layout_json, :quality_score
            )
            """,
            params,
        )
        return self._last_insert_id(cur)

    @staticmethod
    def _feature_params(feature: JsonDict) -> SQLiteParams:
        return {
            "path": str(feature["path"]),
            "feature_revision": str(feature["feature_revision"]),
            "file_dev": Storage._sqlite_identity_value(feature["file_dev"]),
            "file_ino": Storage._sqlite_identity_value(feature["file_ino"]),
            "mtime_ns": int(feature["mtime_ns"]),
            "file_size": int(feature["file_size"]),
            "width": int(feature["width"]),
            "height": int(feature["height"]),
            "pixel_digest": str(feature["pixel_digest"]),
            "phash": str(feature["phash"]),
            "phash_int": str(feature["phash_int"]),
            "dhash": str(feature.get("dhash", "")),
            "whash": str(feature.get("whash", "")),
            "avg_rgb_json": json.dumps(feature["avg_rgb"]),
            "color_hist_json": json.dumps(feature["color_hist"]),
            "hsv_hist_json": json.dumps(feature["hsv_hist"]),
            "luma_layout_json": json.dumps(feature.get("luma_layout", [])),
            "edge_layout_json": json.dumps(feature.get("edge_layout", [])),
            "quality_score": float(feature.get("quality_score", 0.0) or 0.0),
        }

    @staticmethod
    def _identity_values(identity: Any) -> SQLiteParams:
        if isinstance(identity, dict):
            identity_map = cast(dict[str, int], identity)
            return {
                "file_dev": Storage._sqlite_identity_value(identity_map["file_dev"]),
                "file_ino": Storage._sqlite_identity_value(identity_map["file_ino"]),
                "mtime_ns": int(identity_map["mtime_ns"]),
                "file_size": int(identity_map["file_size"]),
            }
        identity_obj = cast(FileIdentityLike, identity)
        return {
            "file_dev": Storage._sqlite_identity_value(identity_obj.file_dev),
            "file_ino": Storage._sqlite_identity_value(identity_obj.file_ino),
            "mtime_ns": int(identity_obj.mtime_ns),
            "file_size": int(identity_obj.file_size),
        }

    @staticmethod
    def sqlite_identity_value(value: Any) -> int | str:
        number = Storage._identity_int(value)
        if SQLITE_MIN_INT <= number <= SQLITE_MAX_INT:
            return number
        return f"{SQLITE_BIG_INT_PREFIX}{number}"

    @staticmethod
    def _sqlite_identity_value(value: Any) -> int | str:
        return Storage.sqlite_identity_value(value)

    @staticmethod
    def _identity_int(value: Any) -> int:
        if isinstance(value, str) and value.startswith(SQLITE_BIG_INT_PREFIX):
            return int(value.removeprefix(SQLITE_BIG_INT_PREFIX))
        return int(value)

    @staticmethod
    def _identity_path(identity: Any) -> str:
        if isinstance(identity, Mapping):
            identity_map = cast(Mapping[str, object], identity)
            return str(identity_map["path"])
        identity_obj = cast(FileIdentityLike, identity)
        return str(identity_obj.path)

    @staticmethod
    def _chunks(values: list[int], size: int) -> Iterator[list[int]]:
        for index in range(0, len(values), size):
            yield values[index : index + size]

    @staticmethod
    def _decode_embedding(blob: bytes | None, embedding_json: str | None) -> list[float] | None:
        if blob:
            values = array("f")
            values.frombytes(blob)
            return [float(value) for value in values]
        return json.loads(embedding_json) if embedding_json else None

    @staticmethod
    def _last_insert_id(cursor: sqlite3.Cursor) -> int:
        if cursor.lastrowid is None:
            raise RuntimeError("SQLite did not return a row id for the insert")
        return cursor.lastrowid

    @staticmethod
    def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
        return (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            is not None
        )

    @staticmethod
    def _table_count(conn: sqlite3.Connection, table: str) -> int:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    @staticmethod
    def _ensure_column(
        conn: sqlite3.Connection, table: str, column: str, definition: str
    ) -> None:
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    @staticmethod
    def _has_image_revision_unique_key(conn: sqlite3.Connection) -> bool:
        for index in conn.execute("PRAGMA index_list(images)").fetchall():
            if not index["unique"]:
                continue
            columns = tuple(
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM pragma_index_info(?) ORDER BY seqno", (index["name"],)
                ).fetchall()
            )
            if columns == ("path", "feature_revision"):
                return True
        return False

    def _restrict_db_permissions(self) -> None:
        for path in (
            self.db_path,
            Path(f"{self.db_path}-wal"),
            Path(f"{self.db_path}-shm"),
        ):
            try:
                path.chmod(0o600)
            except FileNotFoundError:
                continue
            except OSError:
                continue


_storage: Storage | None = None


def get_storage() -> Storage:
    global _storage
    if _storage is None:
        _storage = Storage(get_settings().db_path)
    return _storage


def reset_storage() -> None:
    global _storage
    _storage = None

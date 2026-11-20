"""Shared operation layer for the HTTP API and the command-line interface.

Both transports execute the same functions in this module so the WebUI
(through ``app.api.routes``) and the CLI (through ``app.cli``) share one
code path, one validation chain, and one set of file-safety checks.
Transport-specific concerns stay outside this module:

- HTTP status mapping and the thumbnail LRU cache live in ``app.api.routes``.
- Argument parsing, output formatting, and exit codes live in ``app.cli``.

Functions raise :class:`OperationError` instead of HTTP exceptions so each
transport can translate failures in its own way.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, cast

from app.core.config import get_settings
from app.core.db import get_storage
from app.models.schemas import (
    CancelResponse,
    ClipInfo,
    DeleteResponse,
    DeviceInfo,
    DuplicateGroup,
    FolderDialogResponse,
    GroupFilter,
    GroupSort,
    GroupSummariesResponse,
    GroupSummary,
    GroupsResponse,
    ImageSummary,
    MoveDuplicatesRequest,
    MoveDuplicatesResponse,
    MoveFailure,
    ResetAppDataResponse,
    ScanFailuresResponse,
    ScanFailure,
    ScanListResponse,
    ScanRequest,
    ScanResponse,
    ScanState,
    ScanStatus,
    SystemResponse,
)
from app.services.clip import get_clip_runtime_info
from app.services.device import get_device_info, runtime_summary
from app.services.features import render_verified_snapshot_rgb
from app.services.file_actions import (
    DuplicateMoveRoot,
    create_duplicate_move_root,
    file_operation_lock,
    finalize_claimed_move,
    prepare_snapshot_move_to_duplicate_folder,
    revalidate_claimed_move,
    rollback_claimed_move,
    snapshot_file_status,
    validate_move_selection,
)
from app.services.folder_dialog import FolderDialogUnavailable, select_scan_folder
from app.services.scanner import scan_manager

JsonDict = dict[str, Any]
TERMINAL_SCAN_STATUSES = {"completed", "failed", "cancelled"}
ThumbnailCacheKey = tuple[str, str, str, int, int | str, int | str, int, int]
MAX_THUMBNAIL_CACHE_ITEMS = 256
_thumbnail_cache: OrderedDict[ThumbnailCacheKey, bytes] = OrderedDict()
_thumbnail_cache_lock = threading.RLock()


class OperationError(Exception):
    """A failed operation with an HTTP-style status code and detail message."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def get_system_info() -> SystemResponse:
    settings = get_settings()
    runtime = runtime_summary()
    device = get_device_info()
    clip = get_clip_runtime_info()
    return SystemResponse(
        python=runtime["python"],
        platform=runtime["platform"],
        app_dir=str(settings.app_dir),
        db_path=str(settings.db_path),
        device=DeviceInfo(
            selected=device.selected,
            available=device.available,
            cuda_devices=device.cuda_devices,
            mps_available=device.mps_available,
            torch_available=device.torch_available,
            torch_version=device.torch_version,
        ),
        clip=ClipInfo(available=clip.available, version=clip.version, error=clip.error),
        default_model=settings.default_clip_model,
        default_pretrained=settings.default_clip_pretrained,
    )


def reset_app_data(*, confirm: bool) -> ResetAppDataResponse:
    if not confirm:
        raise OperationError(400, "Explicit confirmation is required")
    with file_operation_lock:
        try:
            counts = get_storage().reset_app_data()
        except ValueError as exc:
            raise OperationError(409, str(exc)) from exc
        clear_thumbnail_cache()
    return ResetAppDataResponse(**counts)


def choose_scan_folder(initial_path: str | None) -> FolderDialogResponse:
    try:
        path = select_scan_folder(initial_path)
    except FolderDialogUnavailable as exc:
        raise OperationError(503, str(exc)) from exc
    return FolderDialogResponse(path=path, cancelled=path is None)


def start_scan(request: ScanRequest) -> ScanResponse:
    try:
        scan_id = scan_manager.start(request)
    except ValueError as exc:
        raise OperationError(422, str(exc)) from exc
    return ScanResponse(id=scan_id, status="queued")


def wait_for_scan(scan_id: int, *, timeout_seconds: float, poll_seconds: float = 0.5) -> ScanState:
    """Block until a scan reaches a terminal status or the timeout expires."""
    deadline = time.monotonic() + max(timeout_seconds, 0)
    state = get_scan_state(scan_id)
    while state.status not in TERMINAL_SCAN_STATUSES:
        if time.monotonic() >= deadline:
            raise OperationError(
                504, f"Timed out waiting for scan {scan_id} (status: {state.status})"
            )
        time.sleep(poll_seconds)
        state = get_scan_state(scan_id)
    return state


def list_scans(limit: int, offset: int, status: ScanStatus | None) -> ScanListResponse:
    settings = get_settings()
    page_size = min(limit, settings.max_scan_page_size)
    rows, total = get_storage().list_scans(page_size, offset, status)
    return ScanListResponse(
        scans=[scan_state(row) for row in rows],
        total=total,
        limit=page_size,
        offset=offset,
    )


def get_scan_state(scan_id: int) -> ScanState:
    row = get_storage().get_scan(scan_id)
    if not row:
        raise OperationError(404, "Scan not found")
    return scan_state(row)


def cancel_scan(scan_id: int) -> CancelResponse:
    row = get_storage().get_scan(scan_id)
    if not row:
        raise OperationError(404, "Scan not found")
    if row["status"] not in {"queued", "running"}:
        return CancelResponse(id=scan_id, status=row["status"])
    if not scan_manager.cancel(scan_id):
        updated = get_storage().get_scan(scan_id)
        if updated and updated["status"] not in {"queued", "running"}:
            return CancelResponse(id=scan_id, status=updated["status"])
        raise OperationError(409, "Scan could not be cancelled")
    updated = get_storage().get_scan(scan_id)
    return CancelResponse(id=scan_id, status=updated["status"] if updated else "cancelled")


def delete_scan(scan_id: int) -> DeleteResponse:
    with file_operation_lock:
        try:
            deleted = get_storage().delete_scan(scan_id)
        except ValueError as exc:
            raise OperationError(409, str(exc)) from exc
    if not deleted:
        raise OperationError(404, "Scan not found")
    return DeleteResponse(deleted=True)


def list_groups(scan_id: int, limit: int, offset: int) -> GroupsResponse:
    storage = get_storage()
    scan = storage.get_scan(scan_id)
    if not scan:
        raise OperationError(404, "Scan not found")
    settings = get_settings()
    page_size = min(limit, settings.max_group_page_size)
    group_rows, total = storage.list_groups_page(scan_id, page_size, offset)
    groups = [group_response(group, scan["path"]) for group in group_rows]
    return GroupsResponse(
        scan_id=scan_id,
        groups=groups,
        total=total,
        limit=page_size,
        offset=offset,
    )


def list_group_summaries(
    scan_id: int,
    limit: int,
    offset: int,
    filter: GroupFilter,
    sort: GroupSort,
) -> GroupSummariesResponse:
    storage = get_storage()
    scan = storage.get_scan(scan_id)
    if not scan:
        raise OperationError(404, "Scan not found")
    settings = get_settings()
    page_size = min(limit, settings.max_group_page_size)
    groups, total = storage.list_group_summaries_page(
        scan_id,
        page_size,
        offset,
        filter,
        sort,
    )
    return GroupSummariesResponse(
        scan_id=scan_id,
        groups=[GroupSummary(**group) for group in groups],
        total=total,
        limit=page_size,
        offset=offset,
        filter=filter,
        sort=sort,
    )


def get_group_detail(scan_id: int, group_id: int) -> DuplicateGroup:
    storage = get_storage()
    scan = storage.get_scan(scan_id)
    if not scan:
        raise OperationError(404, "Scan not found")
    group = storage.get_group(scan_id, group_id)
    if not group:
        raise OperationError(404, "Group not found")
    return group_response(group, scan["path"])


def list_failures(scan_id: int, limit: int, offset: int) -> ScanFailuresResponse:
    storage = get_storage()
    if not storage.get_scan(scan_id):
        raise OperationError(404, "Scan not found")
    page_size = min(limit, get_settings().max_scan_page_size)
    rows, total = storage.list_failures(scan_id, page_size, offset)
    return ScanFailuresResponse(
        scan_id=scan_id,
        failures=[ScanFailure(**row) for row in rows],
        total=total,
        limit=page_size,
        offset=offset,
    )


def export_thumbnail(image_id: int, destination: Path) -> Path:
    """Render a verified thumbnail to a file. Returns the destination path."""
    storage = get_storage()
    row = storage.get_scan_image(image_id)
    if not row:
        raise OperationError(404, "Image not found")
    scan = storage.get_scan(int(row["scan_id"]))
    if not scan:
        raise OperationError(404, "Scan not found")
    file_status = snapshot_file_status(row, scan["path"], verify_digest=False)
    if not file_status.available:
        raise OperationError(409, file_status.reason or file_status.status)
    try:
        thumb = render_verified_snapshot_rgb(row)
        thumb.thumbnail(
            (get_settings().thumbnail_size, get_settings().thumbnail_size)
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        thumb.save(destination, format="JPEG", quality=86)
    except Exception as exc:
        raise OperationError(409, f"Unable to create thumbnail: {exc}") from exc
    return destination


def move_duplicates(request: MoveDuplicatesRequest) -> MoveDuplicatesResponse:
    if not request.confirm:
        raise OperationError(400, "Explicit confirmation is required")
    with file_operation_lock:
        return _move_duplicate_files_locked(request)


def _move_duplicate_files_locked(request: MoveDuplicatesRequest) -> MoveDuplicatesResponse:
    storage = get_storage()
    scan = storage.get_scan(request.scan_id)
    if not scan:
        raise OperationError(404, "Scan not found")
    group = storage.get_group(request.scan_id, request.group_id)
    if not group:
        raise OperationError(404, "Group not found")

    member_ids = {int(image["id"]) for image in group["images"]}
    selected_ids = {int(image_id) for image_id in request.image_ids}
    if not selected_ids:
        return MoveDuplicatesResponse(moved=[], skipped=[], failures=[])
    if not selected_ids.issubset(member_ids):
        raise OperationError(400, "Selection contains images outside the group")

    statuses = {
        int(image["id"]): snapshot_file_status(image, scan["path"], verify_digest=False)
        for image in group["images"]
    }
    available_ids = {image_id for image_id, status in statuses.items() if status.available}
    try:
        validate_move_selection(member_ids, selected_ids, available_ids)
    except ValueError as exc:
        raise OperationError(400, str(exc)) from exc

    moved: list[int] = []
    skipped: list[int] = []
    failures: list[MoveFailure] = []
    destination_root: DuplicateMoveRoot | Path | None = None
    image_by_id = {int(image["id"]): image for image in group["images"]}
    unselected_ids = member_ids - selected_ids
    for image_id in sorted(selected_ids):
        image = image_by_id[image_id]
        current_status = snapshot_file_status(image, scan["path"], verify_digest=True)
        if not current_status.available:
            skipped.append(image_id)
            failures.append(
                MoveFailure(image_id=image_id, reason=current_status.reason or current_status.status)
            )
            continue
        if not _has_available_unselected_survivor(
            unselected_ids,
            image_by_id,
            scan["path"],
        ):
            skipped.append(image_id)
            failures.append(
                MoveFailure(
                    image_id=image_id,
                    reason="Refusing to move the last currently available image in the group",
                )
            )
            continue
        try:
            if destination_root is None:
                destination_root = create_duplicate_move_root(scan["path"])
            claimed_move = prepare_snapshot_move_to_duplicate_folder(
                image,
                scan["path"],
                destination_root,
            )
            moved_path = claimed_move.path
        except Exception as exc:
            skipped.append(image_id)
            failures.append(MoveFailure(image_id=image_id, reason=str(exc)))
            continue
        after_move_status = snapshot_file_status(image, scan["path"], verify_digest=False)
        if after_move_status.available:
            rollback_error = rollback_claimed_move(image, scan["path"], claimed_move)
            if rollback_error is not None:
                raise OperationError(
                    500,
                    "The original file reappeared and the duplicate-folder copy could not be "
                    f"removed. Recover or review {moved_path}: {rollback_error}",
                )
            skipped.append(image_id)
            failures.append(
                MoveFailure(
                    image_id=image_id,
                    reason="File still exists at the original path after duplicate-folder move",
                )
            )
            continue
        if not moved_path.exists():
            rollback_error = rollback_claimed_move(image, scan["path"], claimed_move)
            if rollback_error is not None:
                raise OperationError(
                    500,
                    f"Claimed destination disappeared and {moved_path} could not be restored: "
                    f"{rollback_error}",
                )
            skipped.append(image_id)
            failures.append(
                MoveFailure(
                    image_id=image_id,
                    reason="Moved file was not found in the duplicate folder",
                )
            )
            continue
        if not _has_available_unselected_survivor(
            unselected_ids,
            image_by_id,
            scan["path"],
        ):
            rollback_error = rollback_claimed_move(image, scan["path"], claimed_move)
            if rollback_error is not None:
                raise OperationError(
                    500,
                    "The last unselected image disappeared and the current file could not be "
                    f"restored. Recover it from {moved_path}: {rollback_error}",
                )
            skipped.append(image_id)
            failures.append(
                MoveFailure(
                    image_id=image_id,
                    reason=(
                        "The last unselected image disappeared during the move; "
                        "the current file was restored"
                    ),
                )
            )
            continue
        try:
            revalidate_claimed_move(image, destination_root, claimed_move)
        except Exception as exc:
            rollback_error = rollback_claimed_move(image, scan["path"], claimed_move)
            if rollback_error is not None:
                raise OperationError(
                    500,
                    f"Destination integrity failed and {moved_path} could not be restored: "
                    f"{exc}; {rollback_error}",
                ) from exc
            skipped.append(image_id)
            failures.append(
                MoveFailure(
                    image_id=image_id,
                    reason=f"Destination integrity failed; the file was restored: {exc}",
                )
            )
            continue
        try:
            storage.mark_moved_and_prune([image_id])
        except Exception as exc:
            rollback_error = rollback_claimed_move(image, scan["path"], claimed_move)
            if rollback_error is not None:
                raise OperationError(
                    500,
                    f"Database update failed and {moved_path} could not be restored: "
                    f"{exc}; {rollback_error}",
                ) from exc
            skipped.append(image_id)
            failures.append(
                MoveFailure(
                    image_id=image_id,
                    reason=f"Database update failed; the file was restored: {exc}",
                )
            )
            continue
        try:
            finalize_claimed_move(
                image,
                destination_root,
                claimed_move,
                verify_destination=False,
            )
        except Exception as exc:
            raise OperationError(
                500,
                f"Move committed but staging cleanup failed at {claimed_move.staging_path}: {exc}",
            ) from exc
        moved.append(image_id)
    if moved:
        clear_thumbnail_cache()
    return MoveDuplicatesResponse(
        moved=moved,
        skipped=skipped,
        failures=failures,
        destination_folder=str(destination_root) if destination_root else None,
    )


def _has_available_unselected_survivor(
    unselected_ids: set[int],
    image_by_id: dict[int, JsonDict],
    scan_path: str,
) -> bool:
    for candidate_id in sorted(unselected_ids):
        candidate_status = snapshot_file_status(
            image_by_id[candidate_id],
            scan_path,
            verify_digest=True,
        )
        if candidate_status.available:
            return True
    return False


def scan_state(row: JsonDict) -> ScanState:
    status = str(row["status"])
    phase = str(row.get("phase") or ("queued" if status not in TERMINAL_SCAN_STATUSES else status))
    return ScanState(
        id=int(row["id"]),
        status=cast(Any, status),
        phase=cast(Any, phase),
        progress_detail=str(row.get("progress_detail") or ""),
        path=row["path"],
        recursive=bool(row["recursive"]),
        model=row["model"],
        pretrained=row["pretrained"],
        device=row["device"],
        thresholds=row.get("thresholds", {}),
        progress=float(row["progress"]),
        total_files=int(row["total_files"]),
        processed_files=int(row["processed_files"]),
        succeeded_files=int(row.get("succeeded_files", 0)),
        failed_files=int(row.get("failed_files", 0)),
        skipped_files=int(row.get("skipped_files", 0)),
        candidate_pairs=int(row["candidate_pairs"]),
        duplicate_groups=int(row["duplicate_groups"]),
        error=row["error"],
        warnings=list(row.get("warnings", [])),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        can_cancel=row["status"] in {"queued", "running"},
    )


def group_response(group: JsonDict, scan_path: str) -> DuplicateGroup:
    recommended_keep_id = int(group["recommended_keep_id"])
    images: list[ImageSummary] = []
    for image in cast(list[JsonDict], group["images"]):
        status = snapshot_file_status(image, scan_path, verify_digest=True)
        images.append(
            ImageSummary(
                id=int(image["id"]),
                path=image["path"],
                width=int(image["width"]),
                height=int(image["height"]),
                pixel_count=int(image["width"]) * int(image["height"]),
                size_bytes=int(image["file_size"]),
                phash=image["phash"],
                avg_rgb=[float(value) for value in image["avg_rgb"]],
                score=float(image["score"]),
                reason=image["reason"],
                is_recommended_keep=int(image["id"]) == recommended_keep_id,
                moved=status.status in {"moved", "trashed"},
                trashed=status.status in {"moved", "trashed"},
                status=status.status,
                available=status.available,
                evidence=image.get("evidence") if image.get("evidence") else None,
            )
        )
    return DuplicateGroup(
        id=int(group["id"]),
        score=float(group["score"]),
        reason=group["reason"],
        recommended_keep_id=recommended_keep_id,
        images=images,
    )


def thumbnail_cache_key(row: JsonDict, thumbnail_size: int) -> ThumbnailCacheKey:
    return (
        str(row["path"]),
        str(row["pixel_digest"]),
        str(row.get("feature_revision", "")),
        int(thumbnail_size),
        cast(int | str, row["file_dev"]),
        cast(int | str, row["file_ino"]),
        int(row["mtime_ns"]),
        int(row["file_size"]),
    )


def thumbnail_cache_get(key: ThumbnailCacheKey) -> bytes | None:
    with _thumbnail_cache_lock:
        content = _thumbnail_cache.get(key)
        if content is None:
            return None
        _thumbnail_cache.move_to_end(key)
        return content


def thumbnail_cache_set(key: ThumbnailCacheKey, content: bytes) -> None:
    with _thumbnail_cache_lock:
        _thumbnail_cache[key] = content
        _thumbnail_cache.move_to_end(key)
        while len(_thumbnail_cache) > MAX_THUMBNAIL_CACHE_ITEMS:
            _thumbnail_cache.popitem(last=False)


def clear_thumbnail_cache() -> None:
    with _thumbnail_cache_lock:
        _thumbnail_cache.clear()


def get_thumbnail_cache_size() -> int:
    """Return the number of items in the thumbnail cache. Useful for testing."""
    with _thumbnail_cache_lock:
        return len(_thumbnail_cache)

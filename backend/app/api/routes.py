from __future__ import annotations

import asyncio
import io
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from app.core.config import get_settings
from app.core.db import get_storage
from app.core.security import require_api_token
from app.models.schemas import (
    CancelResponse,
    DeleteResponse,
    FolderDialogRequest,
    FolderDialogResponse,
    GroupFilter,
    GroupSort,
    GroupSummariesResponse,
    GroupsResponse,
    DuplicateGroup,
    MoveDuplicatesRequest,
    MoveDuplicatesResponse,
    ResetAppDataRequest,
    ResetAppDataResponse,
    ScanFailuresResponse,
    ScanListResponse,
    ScanRequest,
    ScanResponse,
    ScanState,
    ScanStatus,
    SystemResponse,
    TrashRequest,
    TrashResponse,
)
from app.services import operations
from app.services.features import render_verified_snapshot_rgb
from app.services.file_actions import snapshot_file_status
from app.services.operations import (
    OperationError,
    clear_thumbnail_cache,
    get_thumbnail_cache_size,
    scan_state,
    thumbnail_cache_get,
    thumbnail_cache_key,
    thumbnail_cache_set,
)

router = APIRouter(dependencies=[Depends(require_api_token)])

TERMINAL_SCAN_STATUSES = operations.TERMINAL_SCAN_STATUSES

__all__ = [
    "router",
    "clear_thumbnail_cache",
    "get_thumbnail_cache_size",
]


def _http_error(exc: OperationError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


@router.get("/system", response_model=SystemResponse)
def system() -> SystemResponse:
    return operations.get_system_info()


@router.post("/system/reset", response_model=ResetAppDataResponse)
def reset_app_data(request: ResetAppDataRequest) -> ResetAppDataResponse:
    try:
        return operations.reset_app_data(confirm=request.confirm)
    except OperationError as exc:
        raise _http_error(exc) from exc


@router.post("/dialogs/folder", response_model=FolderDialogResponse)
def choose_folder(request: FolderDialogRequest) -> FolderDialogResponse:
    try:
        return operations.choose_scan_folder(request.initial_path)
    except OperationError as exc:
        raise _http_error(exc) from exc


@router.post("/scans", response_model=ScanResponse)
def create_scan(request: ScanRequest) -> ScanResponse:
    try:
        return operations.start_scan(request)
    except OperationError as exc:
        raise _http_error(exc) from exc


@router.get("/scans", response_model=ScanListResponse)
def list_scans(
    limit: int = Query(default=50, ge=1),
    offset: int = Query(default=0, ge=0),
    status: ScanStatus | None = Query(default=None),
) -> ScanListResponse:
    return operations.list_scans(limit, offset, status)


@router.get("/scans/{scan_id}", response_model=ScanState)
def get_scan(scan_id: int) -> ScanState:
    try:
        return operations.get_scan_state(scan_id)
    except OperationError as exc:
        raise _http_error(exc) from exc


@router.post("/scans/{scan_id}/cancel", response_model=CancelResponse)
def cancel_scan(scan_id: int) -> CancelResponse:
    try:
        return operations.cancel_scan(scan_id)
    except OperationError as exc:
        raise _http_error(exc) from exc


@router.delete("/scans/{scan_id}", response_model=DeleteResponse)
def delete_scan(scan_id: int) -> DeleteResponse:
    try:
        return operations.delete_scan(scan_id)
    except OperationError as exc:
        raise _http_error(exc) from exc


@router.get("/scans/{scan_id}/events")
async def scan_events(scan_id: int) -> StreamingResponse:
    storage = get_storage()
    if not await asyncio.to_thread(storage.get_scan, scan_id):
        raise HTTPException(status_code=404, detail="Scan not found")

    async def event_stream():
        last_payload = None
        idle_ticks = 0
        while True:
            row = await asyncio.to_thread(storage.get_scan, scan_id)
            if not row:
                yield "event: error\ndata: {\"error\":\"scan not found\"}\n\n"
                return
            payload = scan_state(row).model_dump()
            encoded = json.dumps(payload)
            if encoded != last_payload:
                yield f"data: {encoded}\n\n"
                last_payload = encoded
                idle_ticks = 0
            else:
                idle_ticks += 1
                if idle_ticks >= 20:
                    yield ": keep-alive\n\n"
                    idle_ticks = 0
            if row["status"] in {"completed", "failed", "cancelled"}:
                return
            await asyncio.sleep(0.75)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/scans/{scan_id}/groups", response_model=GroupsResponse)
def get_groups(
    scan_id: int,
    limit: int = Query(default=100, ge=1),
    offset: int = Query(default=0, ge=0),
) -> GroupsResponse:
    try:
        return operations.list_groups(scan_id, limit, offset)
    except OperationError as exc:
        raise _http_error(exc) from exc


@router.get("/scans/{scan_id}/group-summaries", response_model=GroupSummariesResponse)
def get_group_summaries(
    scan_id: int,
    limit: int = Query(default=100, ge=1),
    offset: int = Query(default=0, ge=0),
    filter: GroupFilter = Query(default="all"),
    sort: GroupSort = Query(default="recommended"),
) -> GroupSummariesResponse:
    try:
        return operations.list_group_summaries(scan_id, limit, offset, filter, sort)
    except OperationError as exc:
        raise _http_error(exc) from exc


@router.get("/scans/{scan_id}/groups/{group_id}", response_model=DuplicateGroup)
def get_group_detail(scan_id: int, group_id: int) -> DuplicateGroup:
    try:
        return operations.get_group_detail(scan_id, group_id)
    except OperationError as exc:
        raise _http_error(exc) from exc


@router.get("/scans/{scan_id}/failures", response_model=ScanFailuresResponse)
def get_failures(
    scan_id: int,
    limit: int = Query(default=100, ge=1),
    offset: int = Query(default=0, ge=0),
) -> ScanFailuresResponse:
    try:
        return operations.list_failures(scan_id, limit, offset)
    except OperationError as exc:
        raise _http_error(exc) from exc


@router.get("/images/{image_id}/thumbnail")
def image_thumbnail(image_id: int, request: Request) -> Response:
    storage = get_storage()
    row = storage.get_scan_image(image_id)
    if not row:
        raise HTTPException(status_code=404, detail="Image not found")
    scan = storage.get_scan(int(row["scan_id"]))
    if not scan:
        raise HTTPException(status_code=404, detail="Scan not found")
    file_status = snapshot_file_status(row, scan["path"], verify_digest=False)
    if not file_status.available:
        raise HTTPException(status_code=409, detail=file_status.reason or file_status.status)

    settings = get_settings()
    feature_revision = row.get("feature_revision", "")
    etag = f'"{row["pixel_digest"]}-{feature_revision}-{settings.thumbnail_size}"'
    cache_key = thumbnail_cache_key(row, settings.thumbnail_size)
    if request.headers.get("if-none-match") == etag:
        verified_status = snapshot_file_status(row, scan["path"], verify_digest=True)
        if not verified_status.available:
            raise HTTPException(
                status_code=409, detail=verified_status.reason or verified_status.status
            )
        return Response(
            status_code=304,
            headers={"ETag": etag, "Cache-Control": "private, no-cache"},
        )
    cached = thumbnail_cache_get(cache_key)
    if cached is not None:
        verified_status = snapshot_file_status(row, scan["path"], verify_digest=True)
        if not verified_status.available:
            raise HTTPException(
                status_code=409, detail=verified_status.reason or verified_status.status
            )
        return Response(
            content=cached,
            media_type="image/jpeg",
            headers={"ETag": etag, "Cache-Control": "private, no-cache"},
        )
    try:
        thumb = render_verified_snapshot_rgb(row)
        thumb.thumbnail((settings.thumbnail_size, settings.thumbnail_size))
        buffer = io.BytesIO()
        thumb.save(buffer, format="JPEG", quality=86)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=f"Unable to create thumbnail: {exc}") from exc
    content = buffer.getvalue()
    thumbnail_cache_set(cache_key, content)
    return Response(
        content=content,
        media_type="image/jpeg",
        headers={"ETag": etag, "Cache-Control": "private, no-cache"},
    )


@router.post("/files/move-duplicates", response_model=MoveDuplicatesResponse)
def move_duplicate_files(request: MoveDuplicatesRequest) -> MoveDuplicatesResponse:
    try:
        return operations.move_duplicates(request)
    except OperationError as exc:
        raise _http_error(exc) from exc


@router.post(
    "/files/trash",
    response_model=TrashResponse,
    deprecated=True,
    summary="Legacy duplicate-folder move endpoint",
    description=(
        "Compatibility alias for /files/move-duplicates. "
        "Files are moved into a newly created duplicate folder, not OS trash."
    ),
)
def trash_files(request: TrashRequest) -> TrashResponse:
    return move_duplicate_files(request)

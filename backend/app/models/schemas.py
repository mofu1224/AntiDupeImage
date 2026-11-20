from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator
from typing_extensions import TypedDict


ScanStatus = Literal["queued", "running", "completed", "failed", "cancelled"]
ScanPhase = Literal[
    "queued",
    "discovering",
    "extracting",
    "matching",
    "verifying",
    "clip",
    "grouping",
    "completed",
    "failed",
    "cancelled",
]
ImageStatus = Literal["available", "moved", "trashed", "missing", "changed", "unsafe"]
PrecisionMode = Literal["exact_pixel", "ultra_strict", "conservative", "balanced", "broad"]
PerformanceProfile = Literal["standard", "lightweight"]
GroupFilter = Literal["all", "visual", "exact"]
GroupSort = Literal["recommended", "score", "files"]


class Thresholds(BaseModel):
    phash_distance: int = Field(default=10, ge=0, le=64)
    histogram_similarity: float = Field(default=0.90, ge=0.0, le=1.0)
    clip_similarity: float = Field(default=0.92, ge=0.0, le=1.0)
    clip_enabled: bool = False
    precision_mode: PrecisionMode = "conservative"
    performance_profile: PerformanceProfile = "standard"


class ScanRequest(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    recursive: bool = True
    model: str | None = Field(default=None, max_length=200)
    pretrained: str | None = Field(default=None, max_length=200)
    device: str = Field(default="auto", max_length=32)
    gpu_index: int | None = Field(default=None, ge=0)
    allow_clip_fallback: bool = True
    thresholds: Thresholds = Field(default_factory=Thresholds)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Path must not be empty")
        return normalized

    @field_validator("model", "pretrained")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("device")
    @classmethod
    def validate_device(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not re.fullmatch(r"(auto|cpu|mps|cuda|cuda:\d+)", normalized):
            raise ValueError("Device must be auto, cpu, mps, cuda, or cuda:<index>")
        return normalized


class ScanResponse(BaseModel):
    id: int
    status: ScanStatus


class FolderDialogRequest(BaseModel):
    initial_path: str | None = Field(default=None, max_length=4096)

    @field_validator("initial_path")
    @classmethod
    def normalize_initial_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class FolderDialogResponse(BaseModel):
    path: str | None = None
    cancelled: bool


class ScanState(BaseModel):
    id: int
    status: ScanStatus
    phase: ScanPhase = "queued"
    progress_detail: str = ""
    path: str
    recursive: bool
    model: str
    pretrained: str
    device: str
    thresholds: Thresholds
    progress: float
    total_files: int
    processed_files: int
    succeeded_files: int
    failed_files: int
    skipped_files: int
    candidate_pairs: int
    duplicate_groups: int
    error: str | None = None
    warnings: list[str] = Field(default_factory=list)
    created_at: str
    updated_at: str
    can_cancel: bool


class ScanListResponse(BaseModel):
    scans: list[ScanState]
    total: int
    limit: int
    offset: int


class DeviceInfo(BaseModel):
    selected: str
    available: list[str]
    cuda_devices: list[str]
    mps_available: bool
    torch_available: bool
    torch_version: str | None = None


class ClipInfo(BaseModel):
    available: bool
    version: str | None = None
    error: str | None = None


class SystemResponse(BaseModel):
    python: str
    platform: str
    app_dir: str
    db_path: str
    device: DeviceInfo
    clip: ClipInfo
    default_model: str
    default_pretrained: str
    token_required: bool = True


class MatchEvidence(TypedDict, total=False):
    verified: bool
    match_type: str
    precision_mode: PrecisionMode
    performance_profile: PerformanceProfile
    reduced_evidence: bool
    candidate_limited: bool
    phash_distance: int
    dhash_distance: int
    whash_distance: int
    histogram_similarity: float
    layout_similarity: float
    structural_similarity: float
    edge_similarity: float
    quality_score: float
    aspect_delta: float
    support_signals: int
    low_information: bool
    small_difference: bool
    pixel_count_delta: float
    clip_similarity: float


class ImageSummary(BaseModel):
    id: int
    path: str
    width: int
    height: int
    pixel_count: int
    size_bytes: int
    phash: str
    avg_rgb: list[float]
    score: float
    reason: str
    is_recommended_keep: bool
    moved: bool = False
    trashed: bool
    status: ImageStatus
    available: bool
    evidence: MatchEvidence | None = None


class DuplicateGroup(BaseModel):
    id: int
    score: float
    reason: str
    recommended_keep_id: int
    images: list[ImageSummary]


class GroupSummary(BaseModel):
    id: int
    score: float
    reason: str
    recommended_keep_id: int
    recommended_keep_path: str
    image_count: int
    moved_count: int


class GroupsResponse(BaseModel):
    scan_id: int
    groups: list[DuplicateGroup]
    total: int
    limit: int
    offset: int


class GroupSummariesResponse(BaseModel):
    scan_id: int
    groups: list[GroupSummary]
    total: int
    limit: int
    offset: int
    filter: GroupFilter
    sort: GroupSort


class ScanFailure(BaseModel):
    id: int
    path: str
    stage: str
    error: str
    created_at: str


class ScanFailuresResponse(BaseModel):
    scan_id: int
    failures: list[ScanFailure]
    total: int
    limit: int
    offset: int


class MoveDuplicatesRequest(BaseModel):
    scan_id: int
    group_id: int
    image_ids: list[int] = Field(max_length=10_000)
    confirm: bool = False

    @field_validator("image_ids")
    @classmethod
    def validate_image_ids(cls, value: list[int]) -> list[int]:
        if any(image_id <= 0 for image_id in value):
            raise ValueError("Image ids must be positive integers")
        if len(set(value)) != len(value):
            raise ValueError("Image ids must not contain duplicates")
        return value


class MoveFailure(BaseModel):
    image_id: int
    reason: str


def _empty_move_failures() -> list[MoveFailure]:
    return []


class MoveDuplicatesResponse(BaseModel):
    moved: list[int]
    skipped: list[int]
    failures: list[MoveFailure] = Field(default_factory=_empty_move_failures)
    destination_folder: str | None = None


TrashRequest = MoveDuplicatesRequest
TrashFailure = MoveFailure
TrashResponse = MoveDuplicatesResponse


class DeleteResponse(BaseModel):
    deleted: bool


class ResetAppDataRequest(BaseModel):
    confirm: bool = False


class ResetAppDataResponse(BaseModel):
    scans_deleted: int
    scan_images_deleted: int
    groups_deleted: int
    group_members_deleted: int
    failures_deleted: int
    cached_images_deleted: int


class CancelResponse(BaseModel):
    id: int
    status: ScanStatus

from __future__ import annotations

import heapq
import threading
import time
from collections.abc import Iterator
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.core.db import Storage, get_storage
from app.models.schemas import ScanRequest
from app.services.clip import ClipEmbedder, clip_revision, get_clip_runtime_info
from app.services.device import select_device
from app.services.features import (
    FEATURE_REVISION,
    LIGHTWEIGHT_FEATURE_REVISION,
    ImageChangedError,
    ImageFeatureError,
    UnsafeImagePathError,
    cosine_similarity,
    dhash_distance,
    edge_similarity,
    extract_image_features,
    feature_sort_score,
    get_file_identity,
    histogram_similarity,
    hydrate_cached_feature,
    is_supported_image,
    layout_similarity,
    mean_color_similarity,
    phash_distance,
    phash_total_distance,
    structural_similarity,
    extract_lightweight_image_features,
    whash_distance,
)
from app.services.file_actions import is_duplicate_move_directory
from app.services.scan_profiles import (
    SMALL_DIFFERENCE_PHASH_SLACK,
    VerificationProfile,
)
from app.services.scan_verifier import (
    calculate_aspect_delta,
    calculate_pixel_count_delta,
    effective_thresholds,
    small_difference_pass,
    small_difference_score,
    structure_override_pass,
    verification_profile,
    verify_candidate_pairs,
)


class ScanCancelled(RuntimeError):
    pass


class CandidatePairLimitError(RuntimeError):
    pass


class CandidateComparisonLimitError(CandidatePairLimitError):
    pass


@dataclass(frozen=True)
class _FeatureInput:
    index: int
    path: Path
    identity: Any
    cached: dict[str, Any] | None


@dataclass(frozen=True)
class _PreparedFeature:
    index: int
    path: Path
    feature: dict[str, Any]


PairKey = tuple[int, int]
PairMeta = dict[str, Any]
PairMap = dict[PairKey, PairMeta]
LIGHTWEIGHT_MAX_CANDIDATES_PER_IMAGE = 8
LIGHTWEIGHT_MAX_FEATURE_WORKERS = 2


class ScanManager:
    def __init__(self, storage: Storage | None = None):
        self.storage = storage or get_storage()
        self.executor: ThreadPoolExecutor | None = self._new_executor()
        self._futures: dict[int, Future[Any]] = {}
        self._cancel_events: dict[int, threading.Event] = {}
        self._lock = threading.RLock()

    def start(self, request: ScanRequest) -> int:
        root = validate_scan_root(request.path)
        settings = get_settings()
        model = request.model or settings.default_clip_model
        pretrained = request.pretrained or settings.default_clip_pretrained
        device = select_device(request.device, request.gpu_index)
        thresholds = effective_thresholds(request.thresholds)
        warnings: list[str] = []

        if (
            request.thresholds.performance_profile == "lightweight"
            and request.thresholds.clip_enabled
        ):
            warnings.append("CLIP was disabled because Lightweight fast mode prioritizes speed.")

        if thresholds.clip_enabled:
            clip_info = get_clip_runtime_info()
            if not clip_info.available:
                if not request.allow_clip_fallback:
                    raise ValueError(
                        f"CLIP was requested but is unavailable: {clip_info.error or 'unknown error'}"
                    )
                thresholds.clip_enabled = False
                warnings.append(
                    f"CLIP was disabled because it is unavailable: {clip_info.error or 'unknown error'}"
                )

        effective_request = request.model_copy(
            update={"path": str(root), "thresholds": thresholds}
        )
        scan_id = self.storage.create_scan(
            path=str(root),
            recursive=request.recursive,
            model=model,
            pretrained=pretrained,
            device=device,
            thresholds=thresholds.model_dump(),
            warnings=warnings,
        )
        cancel_event = threading.Event()
        with self._lock:
            if self.executor is None:
                self.executor = self._new_executor()
            self._cancel_events[scan_id] = cancel_event
            future = self.executor.submit(
                run_scan,
                scan_id,
                effective_request,
                self.storage,
                model,
                pretrained,
                device,
                cancel_event,
            )
            self._futures[scan_id] = future
            future.add_done_callback(lambda completed, target=scan_id: self._cleanup(target, completed))
        return scan_id

    def cancel(self, scan_id: int) -> bool:
        with self._lock:
            event = self._cancel_events.get(scan_id)
            if event:
                event.set()
            future = self._futures.get(scan_id)
        if not self.storage.request_cancel(scan_id):
            return False
        with self._lock:
            if future and future.cancel():
                self.storage.update_scan(
                    scan_id,
                    status="cancelled",
                    progress=1.0,
                    error=None,
                    cancel_requested=0,
                )
        return True

    def recover_interrupted(self) -> int:
        return self.storage.recover_interrupted_scans()

    def shutdown(self) -> None:
        with self._lock:
            for event in self._cancel_events.values():
                event.set()
            executor = self.executor
            self.executor = None
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)

    def _cleanup(self, scan_id: int, future: Future[Any]) -> None:
        with self._lock:
            self._futures.pop(scan_id, None)
            self._cancel_events.pop(scan_id, None)
        if future.cancelled():
            try:
                self.storage.update_scan(
                    scan_id,
                    status="cancelled",
                    phase="cancelled",
                    progress=1.0,
                    error=None,
                    progress_detail="Scan cancelled before it started",
                    cancel_requested=0,
                )
            except Exception:
                pass
            return
        exception = future.exception()
        if exception is None:
            return
        error = str(exception) or exception.__class__.__name__
        try:
            self.storage.update_scan(
                scan_id,
                status="failed",
                phase="failed",
                progress=1.0,
                error=error,
                progress_detail="Scan failed unexpectedly",
                duplicate_groups=0,
                cancel_requested=0,
            )
        except Exception:
            pass

    @staticmethod
    def _new_executor() -> ThreadPoolExecutor:
        return ThreadPoolExecutor(max_workers=1, thread_name_prefix="antidupe-scan")


def validate_scan_root(value: str) -> Path:
    root = Path(value).expanduser()
    if root.is_symlink():
        raise ValueError(f"Symbolic link scan roots are not allowed: {root}")
    try:
        resolved = root.resolve(strict=True)
    except FileNotFoundError as exc:
        raise ValueError(f"Path does not exist: {root}") from exc
    except OSError as exc:
        raise ValueError(f"Unable to access path: {root}: {exc}") from exc
    if resolved.is_file():
        if not is_supported_image(resolved):
            raise ValueError(f"Unsupported image type: {resolved}")
        try:
            get_file_identity(resolved)
        except Exception as exc:
            raise ValueError(str(exc)) from exc
        return resolved
    if not resolved.is_dir():
        raise ValueError(f"Path is not a folder or supported image: {resolved}")
    return resolved


def _update_scan_phase(
    storage: Storage,
    scan_id: int,
    phase: str,
    progress_detail: str,
    **values: Any,
) -> None:
    storage.update_scan(
        scan_id,
        phase=phase,
        progress_detail=progress_detail[:500],
        **values,
    )


def run_scan(
    scan_id: int,
    request: ScanRequest,
    storage: Storage,
    model: str,
    pretrained: str,
    device: str,
    cancel_event: threading.Event | None = None,
) -> None:
    thresholds = effective_thresholds(request.thresholds)
    last_cancel_db_check = 0.0

    def check_cancelled() -> None:
        nonlocal last_cancel_db_check
        if cancel_event and cancel_event.is_set():
            raise ScanCancelled("Scan cancelled")
        now = time.monotonic()
        if now - last_cancel_db_check >= 0.25:
            last_cancel_db_check = now
            if storage.is_cancel_requested(scan_id):
                raise ScanCancelled("Scan cancelled")

    try:
        root = validate_scan_root(request.path)
        if root != Path(request.path):
            raise UnsafeImagePathError(
                f"Scan root resolved to a different path after it was queued: {request.path}"
            )
        _update_scan_phase(
            storage,
            scan_id,
            "discovering",
            f"Discovering images in {root}",
            status="running",
            progress=0.01,
            error=None,
        )
        check_cancelled()

        paths, discovery_failures = _discover_images(root, request.recursive, check_cancelled)
        check_cancelled()
        storage.record_failures_bulk(
            scan_id,
            [(path, "discovery", error) for path, error in discovery_failures],
        )
        total = len(paths)
        skipped = len(discovery_failures)
        storage.update_scan(
            scan_id,
            total_files=total,
            skipped_files=skipped,
            progress=0.05,
        )
        if total == 0:
            storage.create_groups(scan_id, [])
            root_failure = next(
                (
                    error
                    for path, error in discovery_failures
                    if path == str(root) and error.startswith("Unable to read directory:")
                ),
                None,
            )
            storage.update_scan(
                scan_id,
                status="failed" if root_failure else "completed",
                phase="failed" if root_failure else "completed",
                progress_detail=root_failure or "No supported images were found.",
                processed_files=0,
                succeeded_files=0,
                failed_files=0,
                duplicate_groups=0,
                error=root_failure,
                progress=1.0,
                cancel_requested=0,
            )
            return

        _update_scan_phase(
            storage,
            scan_id,
            "extracting",
            f"Extracting features from {total} image(s)",
            total_files=total,
            skipped_files=skipped,
            progress=0.05,
        )
        features, _, _ = _extract_scan_features(
            paths,
            root,
            storage,
            scan_id,
            check_cancelled,
            thresholds.performance_profile,
        )

        if not features:
            storage.create_groups(scan_id, [])
            storage.update_scan(
                scan_id,
                status="failed",
                phase="failed",
                error="No images could be decoded successfully.",
                progress_detail="No images could be decoded successfully.",
                duplicate_groups=0,
                progress=1.0,
                cancel_requested=0,
            )
            return

        check_cancelled()
        _update_scan_phase(
            storage,
            scan_id,
            "matching",
            f"Matching {len(features)} image(s)",
            progress=0.6,
        )
        profile = verification_profile(thresholds)
        max_candidate_pairs = int(getattr(get_settings(), "max_candidate_pairs", 100_000))
        max_candidate_comparisons = int(
            getattr(get_settings(), "max_candidate_comparisons", 1_000_000)
        )
        exact_pairs = _exact_pairs(features, check_cancelled)
        _enforce_candidate_pair_budget(exact_pairs, max_candidate_pairs)
        if thresholds.precision_mode == "exact_pixel":
            raw_pairs = _merge_pair_maps(exact_pairs, {})
        else:
            candidate_pairs = _candidate_pairs(
                features,
                thresholds.phash_distance,
                thresholds.histogram_similarity,
                profile,
                check_cancelled,
                include_small_difference=thresholds.performance_profile == "standard",
                max_candidates_per_image=(
                    LIGHTWEIGHT_MAX_CANDIDATES_PER_IMAGE
                    if thresholds.performance_profile == "lightweight"
                    else None
                ),
                performance_profile=thresholds.performance_profile,
                max_total_pairs=max_candidate_pairs - len(exact_pairs),
                max_total_comparisons=max_candidate_comparisons,
            )
            raw_pairs = _merge_pair_maps(exact_pairs, candidate_pairs)
        _enforce_candidate_pair_budget(raw_pairs, max_candidate_pairs)
        storage.update_scan(
            scan_id,
            candidate_pairs=len(raw_pairs),
            progress=0.72,
            progress_detail=f"Found {len(raw_pairs)} candidate pair(s)",
        )

        check_cancelled()
        _update_scan_phase(
            storage,
            scan_id,
            "verifying",
            f"Verifying {len(raw_pairs)} candidate pair(s)",
            progress=0.72,
        )
        candidate_pairs = verify_candidate_pairs(
            raw_pairs,
            features,
            thresholds,
            check_cancelled,
        )
        storage.update_scan(
            scan_id,
            candidate_pairs=len(candidate_pairs),
            progress=0.76,
            progress_detail=f"{len(candidate_pairs)} candidate pair(s) passed verification",
        )

        if thresholds.clip_enabled:
            try:
                _update_scan_phase(
                    storage,
                    scan_id,
                    "clip",
                    f"Filtering {len(candidate_pairs)} candidate pair(s) with CLIP",
                    progress=0.76,
                )
                candidate_pairs = _apply_clip_filter(
                    scan_id,
                    candidate_pairs,
                    features,
                    storage,
                    model,
                    pretrained,
                    device,
                    thresholds.clip_similarity,
                    check_cancelled,
                    request.allow_clip_fallback,
                )
            except ScanCancelled:
                raise
            except Exception as exc:
                if not request.allow_clip_fallback:
                    raise
                storage.add_scan_warning(
                    scan_id,
                    f"CLIP filtering failed and visual-hash results were used instead: {exc}",
                )
        storage.update_scan(
            scan_id,
            candidate_pairs=len(candidate_pairs),
            progress=0.9,
            progress_detail=f"{len(candidate_pairs)} candidate pair(s) remain after filtering",
        )

        check_cancelled()
        _update_scan_phase(
            storage,
            scan_id,
            "grouping",
            f"Building groups from {len(candidate_pairs)} candidate pair(s)",
            progress=0.9,
        )
        groups = _build_groups(features, candidate_pairs, check_cancelled)
        storage.create_groups(scan_id, groups, check_cancelled)
        check_cancelled()
        storage.update_scan(
            scan_id,
            status="completed",
            phase="completed",
            progress_detail=f"Completed with {len(groups)} duplicate group(s)",
            duplicate_groups=len(groups),
            progress=1.0,
            cancel_requested=0,
        )
    except ScanCancelled:
        storage.create_groups(scan_id, [])
        storage.update_scan(
            scan_id,
            status="cancelled",
            phase="cancelled",
            error=None,
            progress_detail="Scan cancelled",
            duplicate_groups=0,
            progress=1.0,
            cancel_requested=0,
        )
    except Exception as exc:
        try:
            storage.create_groups(scan_id, [])
        except Exception:
            pass
        storage.update_scan(
            scan_id,
            status="failed",
            phase="failed",
            error=str(exc),
            progress_detail="Scan failed",
            duplicate_groups=0,
            progress=1.0,
            cancel_requested=0,
        )


def _extract_scan_features(
    paths: list[Path],
    root: Path,
    storage: Storage,
    scan_id: int,
    check_cancelled: Any,
    performance_profile: str,
) -> tuple[list[dict[str, Any]], int, int]:
    inputs: list[tuple[int, Path, Any]] = []
    total = len(paths)
    processed = 0
    succeeded = 0
    failed = 0
    prepared: list[_PreparedFeature] = []
    failure_records: list[tuple[str, str, str]] = []

    def flush_failures() -> None:
        if not failure_records:
            return
        storage.record_failures_bulk(scan_id, failure_records.copy())
        failure_records.clear()

    def record_feature_failure(path: Path, error: str) -> None:
        failure_records.append((str(path), "features", error))
        if len(failure_records) >= 50:
            flush_failures()

    def update_progress(force: bool = False) -> None:
        if not force and processed != total and processed % 10 != 0:
            return
        storage.update_scan(
            scan_id,
            processed_files=processed,
            succeeded_files=succeeded,
            failed_files=failed,
            progress=0.05 + (0.55 * processed / max(1, total)),
            progress_detail=f"Processed {processed}/{total} image(s)",
        )

    for index, path in enumerate(paths, start=1):
        check_cancelled()
        try:
            identity = get_file_identity(path)
            if not _is_within(Path(identity.path), root):
                raise UnsafeImagePathError(
                    f"Resolved image path is outside the scan root: {identity.path}"
                )
            inputs.append((index, path, identity))
        except ImageFeatureError as exc:
            failed += 1
            processed += 1
            record_feature_failure(path, str(exc))
            update_progress()

    feature_revision = _feature_revision_for_profile(performance_profile)
    cached_by_path = storage.get_cached_images(
        [identity for _, _, identity in inputs],
        feature_revision,
        check_cancelled,
    )
    feature_inputs = [
        _FeatureInput(
            index=index,
            path=path,
            identity=identity,
            cached=cached_by_path.get(str(identity.path)),
        )
        for index, path, identity in inputs
    ]

    workers = _feature_workers_for_profile(performance_profile, len(feature_inputs))

    def accept(item: _FeatureInput, feature: dict[str, Any]) -> None:
        nonlocal processed, succeeded
        prepared.append(_PreparedFeature(index=item.index, path=item.path, feature=feature))
        succeeded += 1
        processed += 1
        update_progress()

    def reject(item: _FeatureInput, exc: ImageFeatureError) -> None:
        nonlocal processed, failed
        failed += 1
        processed += 1
        record_feature_failure(item.path, str(exc))
        update_progress()

    if not feature_inputs:
        flush_failures()
        update_progress(force=True)
        return [], succeeded, failed

    if workers == 1:
        for item in feature_inputs:
            check_cancelled()
            try:
                accept(item, _prepare_feature(item, root, performance_profile, check_cancelled))
            except ImageFeatureError as exc:
                reject(item, exc)
        flush_failures()
        update_progress(force=True)
        features, late_failed = _save_prepared_features(
            storage,
            scan_id,
            prepared,
            check_cancelled,
        )
        succeeded -= late_failed
        failed += late_failed
        update_progress(force=True)
        return features, succeeded, failed

    executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="antidupe-feature")
    max_in_flight = workers * 2
    input_iterator = iter(feature_inputs)
    futures: dict[Future[dict[str, Any]], _FeatureInput] = {}

    def submit_next() -> Future[dict[str, Any]] | None:
        try:
            item = next(input_iterator)
        except StopIteration:
            return None
        future = executor.submit(_prepare_feature, item, root, performance_profile, check_cancelled)
        futures[future] = item
        return future

    for _ in range(min(max_in_flight, len(feature_inputs))):
        submit_next()
    pending = set(futures)
    try:
        while pending:
            check_cancelled()
            done, pending = wait(pending, timeout=0.25, return_when=FIRST_COMPLETED)
            for future in done:
                item = futures.pop(future)
                try:
                    accept(item, future.result())
                except ImageFeatureError as exc:
                    reject(item, exc)
            while len(pending) < max_in_flight:
                submitted = submit_next()
                if submitted is None:
                    break
                pending.add(submitted)
    except ScanCancelled:
        for future in pending:
            future.cancel()
        flush_failures()
        raise
    finally:
        executor.shutdown(wait=True, cancel_futures=True)

    flush_failures()
    update_progress(force=True)
    features, late_failed = _save_prepared_features(
        storage,
        scan_id,
        prepared,
        check_cancelled,
    )
    succeeded -= late_failed
    failed += late_failed
    update_progress(force=True)
    return features, succeeded, failed


def _feature_revision_for_profile(performance_profile: str) -> str:
    if performance_profile == "lightweight":
        return LIGHTWEIGHT_FEATURE_REVISION
    return FEATURE_REVISION


def _feature_workers_for_profile(performance_profile: str, feature_count: int) -> int:
    workers = max(1, int(get_settings().feature_workers))
    if performance_profile == "lightweight":
        workers = min(workers, LIGHTWEIGHT_MAX_FEATURE_WORKERS)
    return min(workers, max(1, feature_count))


def _prepare_feature(
    item: _FeatureInput,
    root: Path,
    performance_profile: str,
    check_cancelled: Any,
) -> dict[str, Any]:
    check_cancelled()
    if item.cached is not None:
        current_identity = get_file_identity(item.path)
        if current_identity != item.identity:
            raise ImageChangedError(
                f"Image changed while cached features were being reused: {item.path}"
            )
        feature = hydrate_cached_feature(item.cached)
        identity = current_identity
    else:
        if performance_profile == "lightweight":
            feature = extract_lightweight_image_features(item.path, check_cancelled)
        else:
            feature = extract_image_features(item.path, check_cancelled)
        identity = get_file_identity(Path(feature["path"]))

    if not _is_within(Path(feature["path"]), root):
        raise UnsafeImagePathError(
            f"Resolved image path is outside the scan root: {feature['path']}"
        )
    if not _feature_identity_matches(feature, identity):
        raise ImageChangedError(f"Image changed before scan snapshot was saved: {feature['path']}")
    return feature


def _save_prepared_features(
    storage: Storage,
    scan_id: int,
    prepared: list[_PreparedFeature],
    check_cancelled: Any,
) -> tuple[list[dict[str, Any]], int]:
    ordered = sorted(prepared, key=lambda item: item.index)
    features: list[dict[str, Any]] = []
    failed = 0
    failure_records: list[tuple[str, str, str]] = []
    for item in ordered:
        check_cancelled()
        feature = dict(item.feature)
        try:
            identity = get_file_identity(Path(feature["path"]))
            if not _feature_identity_matches(feature, identity):
                raise ImageChangedError(
                    f"Image changed before scan snapshot was saved: {feature['path']}"
                )
        except ImageFeatureError as exc:
            failed += 1
            failure_records.append((str(item.path), "features", str(exc)))
            continue
        features.append(feature)
    storage.record_failures_bulk(scan_id, failure_records)
    saved_ids = storage.save_scan_images_bulk(scan_id, features, check_cancelled)
    for feature, (cache_image_id, scan_image_id) in zip(features, saved_ids):
        feature["cache_image_id"] = cache_image_id
        feature["image_id"] = scan_image_id
    return features, failed


def _discover_images(
    root: Path, recursive: bool, check_cancelled: Any | None = None
) -> tuple[list[Path], list[tuple[str, str]]]:
    if root.is_file():
        return [root], []

    paths: list[Path] = []
    failures: list[tuple[str, str]] = []
    pending = [root]
    while pending:
        if check_cancelled:
            check_cancelled()
        directory = pending.pop()
        try:
            if directory.is_symlink():
                failures.append((str(directory), "Symbolic links are skipped"))
                continue
            resolved_directory = directory.resolve(strict=True)
            if resolved_directory != directory or not _is_within(resolved_directory, root):
                failures.append(
                    (str(directory), "Directory path resolved outside or changed from the scan root")
                )
                continue
            for child in directory.iterdir():
                if check_cancelled:
                    check_cancelled()
                try:
                    if child.is_symlink():
                        failures.append((str(child), "Symbolic links are skipped"))
                        continue
                    if child.is_dir():
                        if is_duplicate_move_directory(child):
                            continue
                        if recursive:
                            pending.append(child)
                        continue
                    if not child.is_file() or not is_supported_image(child):
                        continue
                    resolved = child.resolve(strict=True)
                    if resolved != child or not _is_within(resolved, root):
                        failures.append(
                            (str(child), "Resolved image path is outside or changed from the scan root")
                        )
                        continue
                    paths.append(resolved)
                except OSError as exc:
                    failures.append((str(child), f"Unable to inspect path: {exc}"))
        except OSError as exc:
            failures.append((str(directory), f"Unable to read directory: {exc}"))
    paths.sort(key=lambda item: (str(item).casefold(), str(item)))
    failures.sort(key=lambda item: (item[0].casefold(), item[0], item[1]))
    return paths, failures


def _exact_pairs(
    features: list[dict[str, Any]], check_cancelled: Any | None = None
) -> dict[tuple[int, int], dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for index, feature in enumerate(features):
        if check_cancelled and index % 256 == 0:
            check_cancelled()
        buckets.setdefault(feature["pixel_digest"], []).append(feature)

    pairs: dict[tuple[int, int], dict[str, Any]] = {}
    for bucket in buckets.values():
        if check_cancelled:
            check_cancelled()
        if len(bucket) < 2:
            continue
        anchor = max(bucket, key=feature_sort_score)
        for member in bucket:
            if check_cancelled:
                check_cancelled()
            if member["image_id"] == anchor["image_id"]:
                continue
            key = _pair_key(anchor["image_id"], member["image_id"])
            pairs[key] = {"score": 1.0, "reason": "exact-pixel"}
    return pairs


def _visual_pairs(  # pyright: ignore[reportUnusedFunction]
    features: list[dict[str, Any]],
    max_phash_distance: int,
    min_histogram_similarity: float,
    check_cancelled: Any | None = None,
) -> PairMap:
    profile = VerificationProfile(
        max_phash_distance=max_phash_distance,
        max_dhash_distance=64,
        max_whash_distance=64,
        min_histogram_similarity=min_histogram_similarity,
        min_layout_similarity=0.0,
        min_structural_similarity=0.0,
        min_edge_similarity=0.0,
        min_quality_score=0.0,
        max_aspect_delta=1.0,
        min_support_signals=0,
    )
    return {
        pair: meta
        for pair, meta in _candidate_pairs(
            features,
            max_phash_distance,
            min_histogram_similarity,
            profile,
            check_cancelled,
            include_small_difference=False,
        ).items()
        if meta["reason"] == "visual-hash"
    }


def _candidate_pairs(
    features: list[dict[str, Any]],
    max_phash_distance: int,
    min_histogram_similarity: float,
    profile: VerificationProfile,
    check_cancelled: Any | None = None,
    *,
    include_small_difference: bool = True,
    max_candidates_per_image: int | None = None,
    performance_profile: str = "standard",
    max_total_pairs: int | None = None,
    max_total_comparisons: int | None = None,
) -> PairMap:
    pair_limit = (
        int(getattr(get_settings(), "max_candidate_pairs", 100_000))
        if max_total_pairs is None
        else max_total_pairs
    )
    comparison_limit = (
        int(getattr(get_settings(), "max_candidate_comparisons", 1_000_000))
        if max_total_comparisons is None
        else max_total_comparisons
    )
    tree = _BKTree()
    pairs: PairMap = {}
    comparison_count = 0

    def consume_comparison_work() -> None:
        nonlocal comparison_count
        comparison_count += 1
        if check_cancelled and comparison_count % 256 == 0:
            check_cancelled()
        if comparison_count > comparison_limit:
            raise CandidateComparisonLimitError(
                f"Candidate comparison safety limit ({comparison_limit}) was reached. "
                "Scan a smaller folder, use stricter thresholds, or select the lightweight "
                "profile."
            )

    digest_members: dict[str, list[int]] = {}
    candidate_features: dict[str, dict[str, Any]] = {}
    for index, feature in enumerate(features):
        if check_cancelled and index % 256 == 0:
            check_cancelled()
        digest = str(feature["pixel_digest"])
        digest_members.setdefault(digest, []).append(int(feature["image_id"]))
        current = candidate_features.get(digest)
        if current is None or feature_sort_score(feature) > feature_sort_score(current):
            candidate_features[digest] = feature

    digest_by_image_id = {
        int(feature["image_id"]): digest for digest, feature in candidate_features.items()
    }

    def add_pair_family(key: PairKey, meta: PairMeta) -> None:
        _check_candidate_pair_capacity(pairs, key, pair_limit)
        pairs[key] = meta
        left_id, right_id = key
        for member_id in digest_members[digest_by_image_id[left_id]]:
            if member_id == left_id:
                continue
            expanded_key = _pair_key(member_id, right_id)
            _check_candidate_pair_capacity(pairs, expanded_key, pair_limit)
            pairs[expanded_key] = meta
        for member_id in digest_members[digest_by_image_id[right_id]]:
            if member_id == right_id:
                continue
            expanded_key = _pair_key(left_id, member_id)
            _check_candidate_pair_capacity(pairs, expanded_key, pair_limit)
            pairs[expanded_key] = meta

    small_max_phash_distance = min(64, profile.max_phash_distance + SMALL_DIFFERENCE_PHASH_SLACK)
    search_phash_distance = max(
        max_phash_distance,
        small_max_phash_distance if include_small_difference else 0,
    )
    total_radius = min(192, (search_phash_distance * 3) + 1)
    for right in candidate_features.values():
        if check_cancelled:
            check_cancelled()
        pair_index = 0
        right_candidates: list[tuple[float, int, int, PairKey, PairMeta]] = []
        accepted_right_candidates = 0
        for left in tree.query(
            right,
            total_radius,
            check_cancelled,
            consume_comparison_work,
        ):
            pair_index += 1
            consume_comparison_work()
            if check_cancelled and pair_index % 256 == 0:
                check_cancelled()
            if left["pixel_digest"] == right["pixel_digest"]:
                continue
            p_distance = phash_distance(left, right)
            if p_distance > search_phash_distance:
                continue
            hist_score = histogram_similarity(left, right)
            small_meta: PairMeta | None = None
            metrics: PairMeta | None = None
            if include_small_difference and p_distance <= small_max_phash_distance:
                metrics = _visual_pair_metrics(left, right, p_distance, hist_score)
            structure_rescue = False
            if metrics is not None:
                support_signals = sum(
                    (
                        int(metrics["dhash_distance"]) <= profile.max_dhash_distance,
                        int(metrics["whash_distance"]) <= profile.max_whash_distance,
                        float(metrics["edge_similarity"]) >= profile.min_edge_similarity,
                    )
                )
                structure_rescue = structure_override_pass(
                    p_distance,
                    float(metrics["mean_color_similarity"]),
                    float(metrics["layout_similarity"]),
                    float(metrics["structural_similarity"]),
                    float(metrics["quality_score"]),
                    float(metrics["aspect_delta"]),
                    support_signals,
                    profile,
                )
            visual_pass = p_distance <= max_phash_distance and (
                hist_score >= min_histogram_similarity or structure_rescue
            )
            if metrics is not None:
                normal_visual_evidence = (
                    p_distance <= profile.max_phash_distance
                    and hist_score >= profile.min_histogram_similarity
                    and float(metrics["layout_similarity"]) >= profile.min_layout_similarity
                    and float(metrics["structural_similarity"]) >= profile.min_structural_similarity
                )
                if not normal_visual_evidence and small_difference_pass(
                    p_distance,
                    int(metrics["dhash_distance"]),
                    int(metrics["whash_distance"]),
                    hist_score,
                    float(metrics["layout_similarity"]),
                    float(metrics["structural_similarity"]),
                    float(metrics["edge_similarity"]),
                    float(metrics["quality_score"]),
                    float(metrics["aspect_delta"]),
                    float(metrics["pixel_count_delta"]),
                    profile,
                ):
                    small_meta = {
                        **metrics,
                        "score": small_difference_score(
                            p_distance,
                            int(metrics["dhash_distance"]),
                            int(metrics["whash_distance"]),
                            hist_score,
                            float(metrics["layout_similarity"]),
                            float(metrics["edge_similarity"]),
                        ),
                        "reason": "small-difference",
                    }
            if small_meta is None and not visual_pass:
                continue
            key = _pair_key(left["image_id"], right["image_id"])
            meta: PairMeta
            if small_meta is not None:
                meta = small_meta
            else:
                if metrics is None:
                    metrics = _visual_pair_metrics(left, right, p_distance, hist_score)
                phash_score = 1.0 - (p_distance / 64.0)
                score = (phash_score * 0.55) + (hist_score * 0.45)
                meta = {
                    **metrics,
                    "score": max(0.0, min(1.0, score)),
                    "reason": "visual-hash",
                }
            if performance_profile == "lightweight":
                meta = {
                    **meta,
                    "performance_profile": "lightweight",
                    "reduced_evidence": True,
                }
            if max_candidates_per_image is None:
                add_pair_family(key, meta)
            else:
                accepted_right_candidates += 1
                if max_candidates_per_image <= 0:
                    continue
                rank = (float(meta["score"]), -key[0], -key[1])
                entry = (*rank, key, meta)
                if len(right_candidates) < max_candidates_per_image:
                    heapq.heappush(right_candidates, entry)
                elif rank > right_candidates[0][:3]:
                    heapq.heapreplace(right_candidates, entry)
        if max_candidates_per_image is not None and right_candidates:
            limited = accepted_right_candidates > max_candidates_per_image
            selected = sorted(
                ((entry[3], entry[4]) for entry in right_candidates),
                key=lambda item: (-float(item[1]["score"]), item[0][0], item[0][1]),
            )
            for key, meta in selected:
                add_pair_family(key, {**meta, "candidate_limited": limited})
        tree.add(right, consume_comparison_work)
    return pairs


def _check_candidate_pair_capacity(pairs: PairMap, key: PairKey, limit: int) -> None:
    if key in pairs or len(pairs) < limit:
        return
    raise CandidatePairLimitError(
        f"Candidate pair safety limit ({limit}) was reached. "
        "Scan a smaller folder, use stricter thresholds, or select the lightweight profile."
    )


def _enforce_candidate_pair_budget(pairs: PairMap, limit: int) -> None:
    if len(pairs) <= limit:
        return
    raise CandidatePairLimitError(
        f"Candidate pair safety limit ({limit}) was exceeded. "
        "Scan a smaller folder, use stricter thresholds, or select the lightweight profile."
    )


def _merge_pair_maps(
    exact_pairs: PairMap,
    visual_pairs: PairMap,
    small_difference_pairs: PairMap | None = None,
) -> PairMap:
    merged = dict(visual_pairs)
    if small_difference_pairs:
        merged.update(small_difference_pairs)
    merged.update(exact_pairs)
    return merged


def _visual_pair_metrics(
    left: dict[str, Any],
    right: dict[str, Any],
    p_distance: int | None = None,
    hist_score: float | None = None,
) -> PairMeta:
    return {
        "phash_distance": phash_distance(left, right) if p_distance is None else p_distance,
        "dhash_distance": dhash_distance(left, right),
        "whash_distance": whash_distance(left, right),
        "histogram_similarity": (
            histogram_similarity(left, right) if hist_score is None else hist_score
        ),
        "mean_color_similarity": mean_color_similarity(left, right),
        "layout_similarity": layout_similarity(left, right),
        "structural_similarity": structural_similarity(left, right),
        "edge_similarity": edge_similarity(left, right),
        "quality_score": min(
            float(left.get("quality_score", 0.0)),
            float(right.get("quality_score", 0.0)),
        ),
        "aspect_delta": calculate_aspect_delta(left, right),
        "pixel_count_delta": calculate_pixel_count_delta(left, right),
    }


def _apply_clip_filter(
    scan_id: int,
    pairs: dict[tuple[int, int], dict[str, Any]],
    features: list[dict[str, Any]],
    storage: Storage,
    model: str,
    pretrained: str,
    device: str,
    threshold: float,
    check_cancelled: Any,
    allow_clip_fallback: bool,
) -> dict[tuple[int, int], dict[str, Any]]:
    feature_by_id = {feature["image_id"]: feature for feature in features}
    needed_ids = sorted(
        {
            image_id
            for pair, meta in pairs.items()
            if meta["reason"] != "exact-pixel"
            for image_id in pair
        }
    )
    embeddings: dict[int, list[float]] = {}
    missing_features: list[dict[str, Any]] = []
    revision = clip_revision(model, pretrained)
    cached_embeddings = storage.get_clip_embeddings(
        [int(feature_by_id[image_id]["cache_image_id"]) for image_id in needed_ids],
        model,
        pretrained,
        revision,
    )
    clip_failures: list[tuple[str, str, str]] = []

    def record_clip_failure(path: str, error: str) -> None:
        clip_failures.append((path, "clip", error))

    def flush_clip_failures() -> None:
        if not clip_failures:
            return
        storage.record_failures_bulk(scan_id, clip_failures.copy())
        clip_failures.clear()

    for scan_image_id in needed_ids:
        check_cancelled()
        feature = feature_by_id[scan_image_id]
        cached_embedding = cached_embeddings.get(int(feature["cache_image_id"]))
        try:
            if not _feature_identity_matches(feature, get_file_identity(Path(feature["path"]))):
                raise ImageChangedError(
                    f"Image changed before CLIP preprocessing: {feature['path']}"
                )
        except Exception as exc:
            record_clip_failure(str(feature["path"]), str(exc))
            if not allow_clip_fallback:
                flush_clip_failures()
                raise RuntimeError(str(exc)) from exc
            storage.add_scan_warning(
                scan_id,
                "CLIP preprocessing skipped image(s) that changed after feature extraction.",
            )
            continue

        if cached_embedding:
            embeddings[scan_image_id] = cached_embedding
        else:
            missing_features.append(feature)

    if missing_features:
        embedder = ClipEmbedder(model, pretrained, device)
        paths = [str(feature["path"]) for feature in missing_features]

        def update_progress(done: int, total: int) -> None:
            storage.update_scan(
                scan_id,
                progress=0.72 + (0.17 * done / max(1, total)),
                progress_detail=f"CLIP embedded {done}/{total} image(s)",
            )

        embedded = embedder.embed_images(
            paths,
            on_progress=update_progress,
            check_cancelled=check_cancelled,
        )
        feature_by_path = {str(feature["path"]): feature for feature in missing_features}
        embeddings_to_cache: list[tuple[int, list[float]]] = []
        for path, vector in embedded.vectors.items():
            feature = feature_by_path[path]
            try:
                if not _feature_identity_matches(feature, get_file_identity(Path(path))):
                    raise ImageChangedError(f"Image changed during CLIP preprocessing: {path}")
            except Exception as exc:
                record_clip_failure(path, str(exc))
                if not allow_clip_fallback:
                    flush_clip_failures()
                    raise RuntimeError(str(exc)) from exc
                storage.add_scan_warning(
                    scan_id,
                    "CLIP preprocessing skipped image(s) that changed after feature extraction.",
                )
                continue
            embeddings_to_cache.append((int(feature["cache_image_id"]), vector))
            embeddings[int(feature["image_id"])] = vector
        storage.set_embeddings_bulk(
            model,
            pretrained,
            embedder.revision,
            embeddings_to_cache,
            check_cancelled,
        )
        if embedded.failures:
            for path, error in embedded.failures.items():
                record_clip_failure(path, error)
            if not allow_clip_fallback:
                flush_clip_failures()
                raise RuntimeError(
                    f"CLIP preprocessing failed for {len(embedded.failures)} image(s)"
                )
            storage.add_scan_warning(
                scan_id,
                f"CLIP preprocessing failed for {len(embedded.failures)} image(s); affected pairs were skipped.",
            )

    flush_clip_failures()
    filtered: dict[tuple[int, int], dict[str, Any]] = {}
    for pair, meta in pairs.items():
        check_cancelled()
        if meta["reason"] == "exact-pixel":
            filtered[pair] = meta
            continue
        left_embedding = embeddings.get(pair[0])
        right_embedding = embeddings.get(pair[1])
        if not left_embedding or not right_embedding:
            continue
        clip_score = cosine_similarity(left_embedding, right_embedding)
        if clip_score >= threshold:
            combined = (meta["score"] * 0.4) + (clip_score * 0.6)
            evidence = dict(meta.get("evidence") or {})
            evidence["clip_similarity"] = round(clip_score, 4)
            filtered[pair] = {
                **meta,
                "score": max(0.0, min(1.0, combined)),
                "reason": "visual-hash+clip",
                "clip_similarity": clip_score,
                "evidence": evidence,
            }
    return filtered


def _build_groups(
    features: list[dict[str, Any]],
    pairs: dict[tuple[int, int], dict[str, Any]],
    check_cancelled: Any | None = None,
) -> list[dict[str, Any]]:
    if not pairs:
        return []
    feature_by_id = {int(feature["image_id"]): feature for feature in features}
    adjacency: dict[int, list[tuple[int, dict[str, Any]]]] = {}
    for index, ((left, right), meta) in enumerate(pairs.items()):
        if check_cancelled and index % 256 == 0:
            check_cancelled()
        adjacency.setdefault(left, []).append((right, meta))
        adjacency.setdefault(right, []).append((left, meta))

    ordered_ids = sorted(feature_by_id, key=lambda image_id: feature_sort_score(feature_by_id[image_id]), reverse=True)
    unassigned = set(adjacency)
    groups: list[dict[str, Any]] = []
    for keep_id in ordered_ids:
        if check_cancelled:
            check_cancelled()
        if keep_id not in unassigned:
            continue
        direct_members = [
            (image_id, meta)
            for image_id, meta in adjacency.get(keep_id, [])
            if image_id in unassigned
        ]
        if not direct_members:
            continue

        member_meta = {image_id: meta for image_id, meta in direct_members}
        member_ids = {keep_id, *member_meta.keys()}
        scores = [float(meta["score"]) for meta in member_meta.values()]
        reasons = {str(meta["reason"]) for meta in member_meta.values()}
        if reasons == {"exact-pixel"}:
            group_reason = "exact-pixel"
        elif "visual-hash+clip" in reasons:
            group_reason = "visual-hash+clip"
        elif "small-difference" in reasons:
            group_reason = "small-difference"
        else:
            group_reason = "visual-hash"
        groups.append(
            {
                "score": min(scores),
                "reason": group_reason,
                "recommended_keep_id": keep_id,
                "members": [
                    {
                        "image_id": keep_id,
                        "score": 1.0,
                        "reason": "recommended-keep",
                    },
                    *[
                        {
                            "image_id": image_id,
                            "score": float(member_meta[image_id]["score"]),
                            "reason": str(member_meta[image_id]["reason"]),
                            "evidence": member_meta[image_id].get("evidence", {}),
                        }
                        for image_id in sorted(member_meta)
                    ],
                ],
            }
        )
        unassigned.difference_update(member_ids)
    groups.sort(key=lambda group: group["score"], reverse=True)
    return groups


def _pair_key(left: int, right: int) -> tuple[int, int]:
    return (left, right) if left < right else (right, left)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _feature_identity_matches(feature: dict[str, Any], identity: Any) -> bool:
    return (
        int(feature["file_dev"]) == int(identity.file_dev)
        and int(feature["file_ino"]) == int(identity.file_ino)
        and int(feature["mtime_ns"]) == int(identity.mtime_ns)
        and int(feature["file_size"]) == int(identity.file_size)
    )


class _BKTree:
    def __init__(self) -> None:
        self.root: _BKNode | None = None

    def add(self, feature: dict[str, Any], consume_work: Any | None = None) -> None:
        if self.root is None:
            self.root = _BKNode(feature)
            return
        node = self.root
        while True:
            if consume_work:
                consume_work()
            distance = phash_total_distance(node.features[0], feature)
            if distance == 0:
                node.features.append(feature)
                return
            child = node.children.get(distance)
            if child is None:
                node.children[distance] = _BKNode(feature)
                return
            node = child

    def query(
        self,
        feature: dict[str, Any],
        radius: int,
        check_cancelled: Any | None = None,
        consume_work: Any | None = None,
    ) -> Iterator[dict[str, Any]]:
        if self.root is None:
            return
        pending = [self.root]
        visited = 0
        while pending:
            node = pending.pop()
            visited += 1
            if check_cancelled and visited % 256 == 0:
                check_cancelled()
            if consume_work:
                consume_work()
            distance = phash_total_distance(node.features[0], feature)
            if distance <= radius:
                yield from node.features
            lower = distance - radius
            upper = distance + radius
            pending.extend(
                child
                for child_distance, child in node.children.items()
                if lower <= child_distance <= upper
            )


class _BKNode:
    def __init__(self, feature: dict[str, Any]) -> None:
        self.features = [feature]
        self.children: dict[int, _BKNode] = {}


scan_manager = ScanManager()

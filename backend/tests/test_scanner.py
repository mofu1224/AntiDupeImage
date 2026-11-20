# pyright: reportPrivateUsage=false

import math
import threading
import time
from concurrent.futures import Future
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

Image = pytest.importorskip("PIL.Image")
ImageDraw = pytest.importorskip("PIL.ImageDraw")

from app.core.db import Storage
from app.models.schemas import ScanRequest, Thresholds
from app.services.clip import EmbedResult
from app.services.features import (
    FileIdentity,
    extract_image_features,
    extract_lightweight_image_features,
    get_file_identity,
)
from app.services.scanner import (
    CandidateComparisonLimitError,
    CandidatePairLimitError,
    ScanCancelled,
    ScanManager,
    _apply_clip_filter,
    _build_groups,
    _candidate_pairs,
    _discover_images,
    _exact_pairs,
    _feature_workers_for_profile,
    _visual_pairs,
    run_scan,
)
from app.services.scan_verifier import (
    _verify_visual_pair,
    effective_thresholds,
    verification_profile,
    verify_candidate_pairs,
)
from app.services.scan_profiles import PRECISION_THRESHOLD_PRESETS


def require_row(row: dict[str, Any] | None) -> dict[str, Any]:
    assert row is not None
    return row


def make_image(path: Path, color: tuple[int, int, int], size: tuple[int, int]) -> None:
    Image.new("RGB", size, color).save(path)


def make_pattern_image(path: Path, size: tuple[int, int] = (128, 128)) -> None:
    image = Image.new("RGB", size, (238, 240, 235))
    draw = ImageDraw.Draw(image)
    width, height = size
    for index in range(0, width, max(8, width // 8)):
        draw.line((index, 0, width - index // 2, height), fill=(25, 85, 170), width=3)
    draw.rectangle(
        (int(width * 0.18), int(height * 0.18), int(width * 0.62), int(height * 0.48)),
        fill=(215, 60, 72),
    )
    draw.ellipse(
        (int(width * 0.45), int(height * 0.42), int(width * 0.84), int(height * 0.82)),
        fill=(38, 145, 94),
    )
    draw.line((0, int(height * 0.74), width, int(height * 0.58)), fill=(30, 30, 30), width=4)
    image.save(path)


def make_local_difference_image(source: Path, target: Path, patch_size: int = 30) -> None:
    image = Image.open(source).copy()
    draw = ImageDraw.Draw(image)
    width, height = image.size
    draw.rectangle(
        (
            width - patch_size - 4,
            height - patch_size - 4,
            width - 4,
            height - 4,
        ),
        fill=(0, 0, 0),
    )
    image.save(target)


def create_scan(storage: Storage, request: ScanRequest) -> int:
    return storage.create_scan(
        request.path,
        request.recursive,
        "ViT-B-32",
        "laion2b_s34b_b79k",
        "cpu",
        request.thresholds.model_dump(),
    )


def test_scan_finds_resized_visual_duplicate_and_reuses_feature_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    first = image_dir / "a.png"
    second = image_dir / "b.jpg"
    make_pattern_image(first, (128, 128))
    Image.open(first).resize((80, 80), Image.Resampling.BILINEAR).save(second, quality=88)
    make_image(image_dir / "c.png", (210, 40, 40), (100, 100))

    storage = Storage(tmp_path / "app.sqlite3")
    storage.init_schema()
    request = ScanRequest(
        path=str(image_dir),
        recursive=True,
        device="cpu",
        thresholds=Thresholds(
            clip_enabled=False,
            histogram_similarity=0.86,
            precision_mode="balanced",
        ),
    )
    scan_id = create_scan(storage, request)
    run_scan(scan_id, request, storage, "ViT-B-32", "laion2b_s34b_b79k", "cpu")

    first = storage.get_scan(scan_id)
    assert first is not None
    assert first["status"] == "completed"
    assert first["phase"] == "completed"
    assert "Completed with" in first["progress_detail"]
    assert first["duplicate_groups"] >= 1
    assert first["succeeded_files"] == 3

    import app.services.scanner as scanner

    calls = 0
    original = scanner.extract_image_features

    def count_calls(path: Path, check_cancelled: Any | None = None) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return original(path, check_cancelled)

    monkeypatch.setattr(scanner, "extract_image_features", count_calls)
    scan_id_2 = create_scan(storage, request)
    run_scan(scan_id_2, request, storage, "ViT-B-32", "laion2b_s34b_b79k", "cpu")

    assert calls == 0
    assert require_row(storage.get_scan(scan_id_2))["status"] == "completed"


def test_parallel_feature_extraction_preserves_snapshot_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    paths = [image_dir / name for name in ("a.png", "b.png", "c.png", "d.png")]
    for index, path in enumerate(paths):
        make_image(path, (20 + index, 80, 140), (40, 40))
    storage = Storage(tmp_path / "app.sqlite3")
    storage.init_schema()
    request = ScanRequest(path=str(image_dir), thresholds=Thresholds(clip_enabled=False))
    scan_id = create_scan(storage, request)

    import app.services.scanner as scanner

    original = scanner.extract_image_features
    delays = {"a.png": 0.03, "b.png": 0.0, "c.png": 0.02, "d.png": 0.01}

    def delayed_extract(path: Path, check_cancelled: Any | None = None) -> dict[str, Any]:
        time.sleep(delays[path.name])
        return original(path, check_cancelled)

    monkeypatch.setattr(scanner, "extract_image_features", delayed_extract)
    monkeypatch.setattr(scanner, "get_settings", lambda: SimpleNamespace(feature_workers=3))

    run_scan(scan_id, request, storage, "ViT-B-32", "laion2b_s34b_b79k", "cpu")

    assert require_row(storage.get_scan(scan_id))["status"] == "completed"
    with storage.connect() as conn:
        rows = conn.execute(
            "SELECT path FROM scan_images WHERE scan_id=? ORDER BY id ASC",
            (scan_id,),
        ).fetchall()
    assert [row["path"] for row in rows] == [str(path.resolve()) for path in paths]


def test_parallel_feature_extraction_cancels_pending_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    paths = [image_dir / f"{index}.png" for index in range(8)]
    for index, path in enumerate(paths):
        make_image(path, (20 + index, 80, 140), (40, 40))
    storage = Storage(tmp_path / "app.sqlite3")
    storage.init_schema()
    request = ScanRequest(path=str(image_dir), thresholds=Thresholds(clip_enabled=False))
    scan_id = create_scan(storage, request)
    cancel_event = threading.Event()

    import app.services.scanner as scanner

    original = scanner.extract_image_features
    calls: list[str] = []
    calls_lock = threading.Lock()

    def blocking_extract(path: Path, check_cancelled: Any | None = None) -> dict[str, Any]:
        with calls_lock:
            calls.append(path.name)
        cancel_event.set()
        time.sleep(0.3)
        return original(path, check_cancelled)

    monkeypatch.setattr(scanner, "extract_image_features", blocking_extract)
    monkeypatch.setattr(scanner, "get_settings", lambda: SimpleNamespace(feature_workers=2))

    run_scan(
        scan_id,
        request,
        storage,
        "ViT-B-32",
        "laion2b_s34b_b79k",
        "cpu",
        cancel_event,
    )

    assert require_row(storage.get_scan(scan_id))["status"] == "cancelled"
    assert len(calls) < len(paths)


def test_parallel_feature_extraction_bounds_unfinished_futures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    paths = [image_dir / f"{index}.png" for index in range(8)]
    for index, path in enumerate(paths):
        make_image(path, (20 + index, 80, 140), (40, 40))
    storage = Storage(tmp_path / "app.sqlite3")
    storage.init_schema()
    request = ScanRequest(path=str(image_dir), thresholds=Thresholds(clip_enabled=False))
    scan_id = create_scan(storage, request)

    import app.services.scanner as scanner

    real_executor = scanner.ThreadPoolExecutor
    original = scanner.extract_image_features
    state = {"unfinished": 0, "peak": 0}
    state_lock = threading.Lock()

    class TrackingExecutor:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.inner = real_executor(*args, **kwargs)

        def submit(self, function: Any, *args: Any, **kwargs: Any) -> Future[Any]:
            with state_lock:
                state["unfinished"] += 1
                state["peak"] = max(state["peak"], state["unfinished"])

            def tracked() -> Any:
                try:
                    return function(*args, **kwargs)
                finally:
                    with state_lock:
                        state["unfinished"] -= 1

            return self.inner.submit(tracked)

        def shutdown(self, *args: Any, **kwargs: Any) -> None:
            self.inner.shutdown(*args, **kwargs)

    def delayed_extract(path: Path, check_cancelled: Any | None = None) -> dict[str, Any]:
        time.sleep(0.03)
        return original(path, check_cancelled)

    monkeypatch.setattr(scanner, "ThreadPoolExecutor", TrackingExecutor)
    monkeypatch.setattr(scanner, "extract_image_features", delayed_extract)
    monkeypatch.setattr(scanner, "get_settings", lambda: SimpleNamespace(feature_workers=2))

    run_scan(scan_id, request, storage, "ViT-B-32", "laion2b_s34b_b79k", "cpu")

    assert require_row(storage.get_scan(scan_id))["status"] == "completed"
    assert state["peak"] == 4


def test_scan_finds_small_local_difference_with_conservative_thresholds(tmp_path: Path) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    first = image_dir / "original.png"
    second = image_dir / "edited.png"
    make_pattern_image(first, (160, 160))
    make_local_difference_image(first, second)

    storage = Storage(tmp_path / "app.sqlite3")
    storage.init_schema()
    request = ScanRequest(
        path=str(image_dir),
        recursive=True,
        device="cpu",
        thresholds=Thresholds(
            clip_enabled=False,
            phash_distance=10,
            histogram_similarity=0.90,
            precision_mode="conservative",
        ),
    )
    scan_id = create_scan(storage, request)
    run_scan(scan_id, request, storage, "ViT-B-32", "laion2b_s34b_b79k", "cpu")

    scan = require_row(storage.get_scan(scan_id))
    assert scan["status"] == "completed"
    assert scan["duplicate_groups"] == 1
    groups = storage.get_groups(scan_id, 10, 0)
    edited = next(
        image for image in groups[0]["images"] if image["path"] == str(second.resolve())
    )
    assert groups[0]["reason"] == "small-difference"
    assert edited["reason"] == "small-difference"
    assert edited["evidence"]["match_type"] == "small-difference"
    assert edited["evidence"]["small_difference"] is True
    assert edited["evidence"]["phash_distance"] > 10
    assert edited["evidence"]["layout_similarity"] >= 0.86


def test_candidate_pairs_reuse_metrics_for_small_difference_path(tmp_path: Path) -> None:
    first = tmp_path / "original.png"
    second = tmp_path / "edited.png"
    make_pattern_image(first, (160, 160))
    make_local_difference_image(first, second)
    features = [extract_image_features(first), extract_image_features(second)]
    for index, feature in enumerate(features, start=1):
        feature["image_id"] = index
    thresholds = Thresholds(
        clip_enabled=False,
        phash_distance=10,
        histogram_similarity=0.90,
        precision_mode="conservative",
    )
    profile = verification_profile(thresholds)

    pairs = _candidate_pairs(features, 10, 0.90, profile)

    assert len(pairs) == 1
    meta = next(iter(pairs.values()))
    assert meta["reason"] == "small-difference"
    assert meta["phash_distance"] > 10
    assert "layout_similarity" in meta
    assert "structural_similarity" in meta
    assert "edge_similarity" in meta
    verified = verify_candidate_pairs(pairs, features, thresholds)
    evidence = next(iter(verified.values()))["evidence"]
    assert evidence["small_difference"] is True
    assert evidence["structural_similarity"] == round(meta["structural_similarity"], 4)


def test_lightweight_candidate_pairs_are_limited_deterministically() -> None:
    features: list[dict[str, Any]] = [
        {
            "image_id": index,
            "pixel_digest": f"digest-{index}",
            "phash": "0:0:0",
            "phash_parts": [0, 0, 0],
            "dhash": "0",
            "whash": "0",
            "color_hist": [1.0],
            "hsv_hist": [1.0],
            "luma_layout": [0],
            "edge_layout": [0],
            "quality_score": 0.5,
            "width": 100,
            "height": 100,
            "file_size": 100,
        }
        for index in range(1, 6)
    ]
    profile = verification_profile(Thresholds(performance_profile="lightweight"))

    pairs = _candidate_pairs(
        features,
        64,
        0.0,
        profile,
        include_small_difference=False,
        max_candidates_per_image=2,
        performance_profile="lightweight",
    )

    assert set(pairs) == {(1, 2), (1, 3), (2, 3), (1, 4), (2, 4), (1, 5), (2, 5)}
    assert any(meta["candidate_limited"] for meta in pairs.values())
    assert {meta["reduced_evidence"] for meta in pairs.values()} == {True}


def test_candidate_pair_generation_stops_at_total_safety_budget() -> None:
    features: list[dict[str, Any]] = [
        {
            "image_id": index,
            "pixel_digest": f"digest-{index}",
            "phash": "0:0:0",
            "phash_parts": [0, 0, 0],
            "dhash": "0",
            "whash": "0",
            "color_hist": [1.0],
            "hsv_hist": [1.0],
            "luma_layout": [0],
            "edge_layout": [0],
            "quality_score": 0.5,
            "width": 100,
            "height": 100,
            "file_size": 100,
        }
        for index in range(1, 5)
    ]
    profile = verification_profile(Thresholds())

    with pytest.raises(CandidatePairLimitError, match=r"safety limit \(3\) was reached"):
        _candidate_pairs(
            features,
            64,
            0.0,
            profile,
            include_small_difference=False,
            max_total_pairs=3,
        )


def test_candidate_pair_generation_bounds_rejected_comparison_work() -> None:
    features: list[dict[str, Any]] = [
        {
            "image_id": index,
            "pixel_digest": f"digest-{index}",
            "phash": "0:0:0",
            "phash_parts": [0, 0, 0],
            "dhash": "0",
            "whash": "0",
            "color_hist": [float(index), 0.0],
            "hsv_hist": [float(index), 0.0],
            "luma_layout": [0],
            "edge_layout": [0],
            "quality_score": 0.5,
            "width": 100,
            "height": 100,
            "file_size": 100,
        }
        for index in range(1, 5)
    ]
    profile = verification_profile(Thresholds())

    with pytest.raises(
        CandidateComparisonLimitError,
        match=r"Candidate comparison safety limit \(3\) was reached",
    ):
        _candidate_pairs(
            features,
            64,
            1.1,
            profile,
            include_small_difference=False,
            max_total_pairs=100,
            max_total_comparisons=3,
        )


def test_candidate_work_budget_counts_bk_tree_query_and_insertion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    features: list[dict[str, Any]] = [
        {
            "image_id": index,
            "pixel_digest": f"digest-{index}",
            "phash": f"{index:x}:0:0",
            "phash_parts": [index, 0, 0],
            "dhash": "0",
            "whash": "0",
            "color_hist": [1.0],
            "hsv_hist": [1.0],
            "luma_layout": [0],
            "edge_layout": [0],
            "quality_score": 0.5,
            "width": 100,
            "height": 100,
            "file_size": 100,
        }
        for index in range(1, 1_201)
    ]
    profile = verification_profile(Thresholds(phash_distance=0))
    distance_calls = 0

    def constant_out_of_radius_distance(
        _left: dict[str, Any], _right: dict[str, Any]
    ) -> int:
        nonlocal distance_calls
        distance_calls += 1
        return 3

    monkeypatch.setattr(
        "app.services.scanner.phash_total_distance",
        constant_out_of_radius_distance,
    )

    with pytest.raises(
        CandidateComparisonLimitError,
        match=r"Candidate comparison safety limit \(1\) was reached",
    ):
        _candidate_pairs(
            features,
            0,
            1.0,
            profile,
            include_small_difference=False,
            max_total_pairs=100,
            max_total_comparisons=1,
        )

    assert distance_calls == 1


def test_exact_digest_bucket_uses_one_anchor_for_visual_candidate_work() -> None:
    exact_features: list[dict[str, Any]] = [
        {
            "image_id": index,
            "pixel_digest": "same-pixels",
            "phash": "0:0:0",
            "phash_parts": [0, 0, 0],
            "dhash": "0",
            "whash": "0",
            "color_hist": [1.0],
            "hsv_hist": [1.0],
            "luma_layout": [0],
            "edge_layout": [0],
            "quality_score": 0.5,
            "width": 100,
            "height": 100,
            "file_size": 100 + index,
        }
        for index in range(1, 1_416)
    ]
    visual_neighbor = {
        **exact_features[0],
        "image_id": 2_000,
        "pixel_digest": "different-pixels",
        "file_size": 10_000,
    }
    features = [*exact_features, visual_neighbor]
    profile = verification_profile(Thresholds())

    exact_pairs = _exact_pairs(features)
    visual_pairs = _candidate_pairs(
        features,
        64,
        0.0,
        profile,
        include_small_difference=False,
        performance_profile="standard",
        max_total_pairs=100_000,
        max_total_comparisons=3,
    )

    assert len(exact_pairs) == 1_414
    assert all(1_415 in pair for pair in exact_pairs)
    assert len(visual_pairs) == 1_415
    assert all(2_000 in pair for pair in visual_pairs)
    groups = _build_groups(features, {**visual_pairs, **exact_pairs})
    assert len(groups) == 1
    assert groups[0]["recommended_keep_id"] == 2_000
    assert len(groups[0]["members"]) == 1_416


def test_run_scan_reserves_total_pair_budget_for_exact_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    for name in ("a.png", "b.png", "c.png"):
        make_image(image_dir / name, (20, 80, 140), (40, 40))
    storage = Storage(tmp_path / "app.sqlite3")
    storage.init_schema()
    request = ScanRequest(path=str(image_dir), thresholds=Thresholds(clip_enabled=False))
    scan_id = create_scan(storage, request)

    import app.services.scanner as scanner

    remaining_budgets: list[int | None] = []

    def capture_candidate_budget(*args: Any, **kwargs: Any) -> dict[tuple[int, int], dict[str, Any]]:
        remaining_budgets.append(kwargs.get("max_total_pairs"))
        return {}

    monkeypatch.setattr(scanner, "_candidate_pairs", capture_candidate_budget)
    monkeypatch.setattr(
        scanner,
        "get_settings",
        lambda: SimpleNamespace(feature_workers=1, max_candidate_pairs=2),
    )

    run_scan(scan_id, request, storage, "ViT-B-32", "laion2b_s34b_b79k", "cpu")

    assert remaining_budgets == [0]
    assert require_row(storage.get_scan(scan_id))["candidate_pairs"] == 2


def test_lightweight_evidence_marks_reduced_and_limited_candidates() -> None:
    features: list[dict[str, Any]] = [
        {
            "image_id": 1,
            "pixel_digest": "left",
            "phash": "0:0:0",
            "phash_parts": [0, 0, 0],
            "color_hist": [1.0],
            "hsv_hist": [1.0],
            "luma_layout": [0],
            "width": 100,
            "height": 100,
            "quality_score": 0.5,
        },
        {
            "image_id": 2,
            "pixel_digest": "right",
            "phash": "0:0:0",
            "phash_parts": [0, 0, 0],
            "color_hist": [1.0],
            "hsv_hist": [1.0],
            "luma_layout": [0],
            "width": 100,
            "height": 100,
            "quality_score": 0.5,
        },
    ]

    verified = verify_candidate_pairs(
        {
            (1, 2): {
                "score": 0.9,
                "reason": "visual-hash",
                "phash_distance": 0,
                "histogram_similarity": 1.0,
                "layout_similarity": 1.0,
                "candidate_limited": True,
                "reduced_evidence": True,
            }
        },
        features,
        Thresholds(performance_profile="lightweight"),
    )

    evidence = verified[(1, 2)]["evidence"]
    assert evidence["performance_profile"] == "lightweight"
    assert evidence["reduced_evidence"] is True
    assert evidence["candidate_limited"] is True


def test_ultra_strict_thresholds_are_forced_to_near_exact_values() -> None:
    thresholds = Thresholds(
        clip_enabled=True,
        precision_mode="ultra_strict",
        phash_distance=64,
        histogram_similarity=0.0,
        clip_similarity=0.0,
    )

    effective = effective_thresholds(thresholds)
    profile = verification_profile(thresholds)

    assert effective.phash_distance == 2
    assert effective.histogram_similarity == 0.99
    assert effective.clip_similarity == 0.99
    assert profile.max_phash_distance == 2
    assert profile.min_histogram_similarity == 0.99
    assert profile.min_layout_similarity == 0.99
    assert profile.min_structural_similarity == 0.995
    assert profile.min_edge_similarity == 0.98
    assert profile.min_support_signals == 3


def test_exact_pixel_thresholds_disable_non_exact_matching() -> None:
    thresholds = Thresholds(
        clip_enabled=True,
        precision_mode="exact_pixel",
        phash_distance=64,
        histogram_similarity=0.0,
        clip_similarity=0.0,
    )

    effective = effective_thresholds(thresholds)
    profile = verification_profile(thresholds)

    assert effective.clip_enabled is False
    assert effective.phash_distance == 0
    assert effective.histogram_similarity == 1.0
    assert effective.clip_similarity == 1.0
    assert profile.max_phash_distance == 0
    assert profile.min_histogram_similarity == 1.0
    assert profile.min_layout_similarity == 1.0
    assert profile.min_structural_similarity == 1.0
    assert profile.min_edge_similarity == 1.0


def test_precision_profiles_relax_monotonically() -> None:
    modes = ("exact_pixel", "ultra_strict", "conservative", "balanced", "broad")
    profiles = [
        verification_profile(
            Thresholds(
                precision_mode=mode,
                **PRECISION_THRESHOLD_PRESETS[mode],
            )
        )
        for mode in modes
    ]

    assert [profile.max_phash_distance for profile in profiles] == sorted(
        profile.max_phash_distance for profile in profiles
    )
    for field in (
        "min_histogram_similarity",
        "min_layout_similarity",
        "min_structural_similarity",
        "min_edge_similarity",
    ):
        values = [getattr(profile, field) for profile in profiles]
        assert values == sorted(values, reverse=True)


def test_lightweight_thresholds_disable_clip() -> None:
    thresholds = Thresholds(
        clip_enabled=True,
        performance_profile="lightweight",
    )

    effective = effective_thresholds(thresholds)

    assert effective.clip_enabled is False
    assert effective.performance_profile == "lightweight"


def test_lightweight_feature_workers_are_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.scanner as scanner

    monkeypatch.setattr(scanner, "get_settings", lambda: SimpleNamespace(feature_workers=6))

    assert _feature_workers_for_profile("lightweight", 10) == 2
    assert _feature_workers_for_profile("standard", 10) == 6


def test_exact_pixel_scan_only_groups_decoded_pixel_matches(tmp_path: Path) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    original = image_dir / "original.png"
    exact_copy = image_dir / "exact-copy.png"
    edited = image_dir / "edited.png"
    make_pattern_image(original, (160, 160))
    Image.open(original).copy().save(exact_copy)
    make_local_difference_image(original, edited)

    storage = Storage(tmp_path / "app.sqlite3")
    storage.init_schema()
    request = ScanRequest(
        path=str(image_dir),
        recursive=True,
        device="cpu",
        thresholds=Thresholds(
            clip_enabled=True,
            precision_mode="exact_pixel",
            phash_distance=64,
            histogram_similarity=0.0,
            clip_similarity=0.0,
        ),
    )
    scan_id = create_scan(storage, request)
    run_scan(scan_id, request, storage, "ViT-B-32", "laion2b_s34b_b79k", "cpu")

    scan = require_row(storage.get_scan(scan_id))
    assert scan["status"] == "completed"
    assert scan["candidate_pairs"] == 1
    assert scan["duplicate_groups"] == 1
    groups = storage.get_groups(scan_id, 10, 0)
    grouped_paths = {image["path"] for image in groups[0]["images"]}
    assert groups[0]["reason"] == "exact-pixel"
    assert grouped_paths == {str(original.resolve()), str(exact_copy.resolve())}
    assert str(edited.resolve()) not in grouped_paths


def test_lightweight_scan_keeps_exact_matches_and_skips_clip_and_small_difference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    original = image_dir / "original.png"
    exact_copy = image_dir / "exact-copy.png"
    make_pattern_image(original, (120, 120))
    Image.open(original).copy().save(exact_copy)
    storage = Storage(tmp_path / "app.sqlite3")
    storage.init_schema()
    request = ScanRequest(
        path=str(image_dir),
        recursive=True,
        device="cpu",
        thresholds=Thresholds(
            clip_enabled=True,
            performance_profile="lightweight",
        ),
    )
    scan_id = create_scan(storage, request)

    def no_clip(*_: object) -> dict[tuple[int, int], dict[str, Any]]:
        raise AssertionError("lightweight scans must not invoke CLIP filtering")

    def no_visual_candidates(
        features: list[dict[str, Any]],
        max_phash_distance: int,
        min_histogram_similarity: float,
        profile: object,
        check_cancelled: object | None = None,
        *,
        include_small_difference: bool = True,
        max_candidates_per_image: int | None = None,
        performance_profile: str = "standard",
        max_total_pairs: int | None = None,
        max_total_comparisons: int | None = None,
    ) -> dict[tuple[int, int], dict[str, Any]]:
        assert include_small_difference is False
        assert max_candidates_per_image == 8
        assert performance_profile == "lightweight"
        assert max_total_pairs == 99_999
        assert max_total_comparisons == 1_000_000
        return {}

    monkeypatch.setattr("app.services.scanner._apply_clip_filter", no_clip)
    monkeypatch.setattr("app.services.scanner._candidate_pairs", no_visual_candidates)

    manager = ScanManager(storage)
    scan_id = manager.start(request)
    with manager._lock:
        future = manager._futures.get(scan_id)
    if future is not None:
        future.result(timeout=5)
    manager.shutdown()

    scan = require_row(storage.get_scan(scan_id))
    assert scan["status"] == "completed"
    assert scan["candidate_pairs"] == 1
    assert scan["duplicate_groups"] == 1
    assert scan["warnings"] == ["CLIP was disabled because Lightweight fast mode prioritizes speed."]
    group = storage.get_groups(scan_id, 10, 0)[0]
    assert group["reason"] == "exact-pixel"
    assert group["images"][1]["evidence"]["performance_profile"] == "lightweight"


def test_ultra_strict_rejects_local_difference_allowed_by_conservative(
    tmp_path: Path,
) -> None:
    first = tmp_path / "original.png"
    second = tmp_path / "edited.png"
    make_pattern_image(first, (160, 160))
    make_local_difference_image(first, second)
    left = extract_image_features(first)
    right = extract_image_features(second)

    conservative = _verify_visual_pair(
        left,
        right,
        {"score": 0.9, "reason": "visual-hash"},
        verification_profile(Thresholds(clip_enabled=False)),
        "conservative",
    )
    ultra_strict = _verify_visual_pair(
        left,
        right,
        {"score": 0.9, "reason": "visual-hash"},
        verification_profile(Thresholds(clip_enabled=False, precision_mode="ultra_strict")),
        "ultra_strict",
    )

    assert conservative is not None
    assert ultra_strict is None


def test_cached_features_are_rejected_if_file_changes_during_reuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    path = image_dir / "a.png"
    make_image(path, (40, 120, 210), (100, 100))
    storage = Storage(tmp_path / "app.sqlite3")
    storage.init_schema()
    feature = extract_image_features(path)
    storage.upsert_image(feature)
    request = ScanRequest(path=str(image_dir), thresholds=Thresholds(clip_enabled=False))
    scan_id = create_scan(storage, request)
    identity = get_file_identity(path)
    changed_identity = replace(identity, mtime_ns=identity.mtime_ns + 1)
    calls = 0

    def changing_identity(target: Path) -> FileIdentity:
        nonlocal calls
        if Path(target) == path.resolve():
            calls += 1
            return identity if calls == 1 else changed_identity
        return get_file_identity(Path(target))

    monkeypatch.setattr("app.services.scanner.get_file_identity", changing_identity)

    run_scan(scan_id, request, storage, "ViT-B-32", "laion2b_s34b_b79k", "cpu")

    scan = require_row(storage.get_scan(scan_id))
    assert scan["status"] == "failed"
    failures, total = storage.list_failures(scan_id, 10, 0)
    assert total == 1
    assert "cached features were being reused" in failures[0]["error"]


def test_scan_rejects_image_that_resolves_outside_root_during_processing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    path = image_dir / "a.png"
    outside = tmp_path / "outside.png"
    make_image(path, (40, 120, 210), (100, 100))
    make_image(outside, (40, 120, 210), (100, 100))
    storage = Storage(tmp_path / "app.sqlite3")
    storage.init_schema()
    request = ScanRequest(path=str(image_dir), thresholds=Thresholds(clip_enabled=False))
    scan_id = create_scan(storage, request)
    outside_identity = get_file_identity(outside)

    def outside_identity_for(_target: Path) -> FileIdentity:
        return outside_identity

    monkeypatch.setattr("app.services.scanner.get_file_identity", outside_identity_for)

    run_scan(scan_id, request, storage, "ViT-B-32", "laion2b_s34b_b79k", "cpu")

    scan = require_row(storage.get_scan(scan_id))
    assert scan["status"] == "failed"
    failures, total = storage.list_failures(scan_id, 10, 0)
    assert total == 1
    assert "outside the scan root" in failures[0]["error"]


def test_discovery_skips_symbolic_links(tmp_path: Path) -> None:
    root = tmp_path / "images"
    root.mkdir()
    outside = tmp_path / "outside.png"
    make_image(outside, (1, 2, 3), (20, 20))
    link = root / "link.png"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"Symbolic links are unavailable in this test environment: {exc}")

    paths, failures = _discover_images(root, recursive=True)

    assert paths == []
    assert failures == [(str(link), "Symbolic links are skipped")]


def test_discovery_skips_generated_duplicate_move_folders(tmp_path: Path) -> None:
    root = tmp_path / "images"
    root.mkdir()
    make_image(root / "original.png", (1, 2, 3), (20, 20))
    moved_folder = root / "AntiDupeImage_Duplicates_20260615_120000"
    moved_folder.mkdir()
    make_image(moved_folder / "moved.png", (1, 2, 3), (20, 20))

    paths, failures = _discover_images(root, recursive=True)

    assert paths == [root / "original.png"]
    assert failures == []


def test_discovery_rejects_directory_replaced_by_symlink(tmp_path: Path) -> None:
    root = tmp_path / "images"
    nested = root / "nested"
    nested.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    make_image(outside / "outside.png", (1, 2, 3), (20, 20))
    moved_nested = tmp_path / "moved-nested"
    nested.rename(moved_nested)
    try:
        nested.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Symbolic links are unavailable in this test environment: {exc}")

    paths, failures = _discover_images(root, recursive=True)

    assert paths == []
    assert failures == [(str(nested), "Symbolic links are skipped")]


def test_scan_rejects_queued_root_that_resolves_elsewhere_and_clears_groups(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "parent"
    root = parent / "images"
    root.mkdir(parents=True)
    path = root / "image.png"
    make_image(path, (1, 2, 3), (20, 20))
    storage = Storage(tmp_path / "app.sqlite3")
    storage.init_schema()
    request = ScanRequest(path=str(root), thresholds=Thresholds(clip_enabled=False))
    scan_id = create_scan(storage, request)
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
    moved_parent = tmp_path / "moved-parent"
    parent.rename(moved_parent)
    try:
        parent.symlink_to(moved_parent, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Symbolic links are unavailable in this test environment: {exc}")

    run_scan(scan_id, request, storage, "ViT-B-32", "laion2b_s34b_b79k", "cpu")

    scan = require_row(storage.get_scan(scan_id))
    assert scan["status"] == "failed"
    assert "resolved to a different path" in scan["error"]
    assert scan["duplicate_groups"] == 0
    assert storage.count_groups(scan_id) == 0


def test_all_decode_failures_mark_scan_failed(tmp_path: Path) -> None:
    root = tmp_path / "images"
    root.mkdir()
    (root / "broken.png").write_text("not an image", encoding="utf-8")
    storage = Storage(tmp_path / "app.sqlite3")
    storage.init_schema()
    request = ScanRequest(path=str(root), thresholds=Thresholds(clip_enabled=False))
    scan_id = create_scan(storage, request)

    run_scan(scan_id, request, storage, "ViT-B-32", "laion2b_s34b_b79k", "cpu")

    scan = require_row(storage.get_scan(scan_id))
    assert scan["status"] == "failed"
    assert scan["failed_files"] == 1
    failures, total = storage.list_failures(scan_id, 10, 0)
    assert total == 1
    assert failures[0]["stage"] == "features"


def test_unreadable_scan_root_marks_scan_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "images"
    root.mkdir()
    storage = Storage(tmp_path / "app.sqlite3")
    storage.init_schema()
    request = ScanRequest(path=str(root), thresholds=Thresholds(clip_enabled=False))
    scan_id = create_scan(storage, request)

    def unreadable_discovery(
        _root: Path, _recursive: bool, _check_cancelled: Any | None = None
    ) -> tuple[list[Path], list[tuple[str, str]]]:
        return [], [(str(root.resolve()), "Unable to read directory: denied")]

    monkeypatch.setattr(
        "app.services.scanner._discover_images",
        unreadable_discovery,
    )

    run_scan(scan_id, request, storage, "ViT-B-32", "laion2b_s34b_b79k", "cpu")

    scan = require_row(storage.get_scan(scan_id))
    assert scan["status"] == "failed"
    assert scan["error"] == "Unable to read directory: denied"


def test_group_members_must_match_recommended_keep_directly() -> None:
    features: list[dict[str, Any]] = [
        {"image_id": 1, "width": 300, "height": 300, "file_size": 300},
        {"image_id": 2, "width": 200, "height": 200, "file_size": 200},
        {"image_id": 3, "width": 100, "height": 100, "file_size": 100},
    ]
    pairs: dict[tuple[int, int], dict[str, Any]] = {
        (1, 2): {"score": 0.95, "reason": "visual-hash"},
        (2, 3): {"score": 0.94, "reason": "visual-hash"},
    }

    groups = _build_groups(features, pairs)

    assert len(groups) == 1
    assert groups[0]["recommended_keep_id"] == 1
    assert {member["image_id"] for member in groups[0]["members"]} == {1, 2}
    assert math.isclose(float(groups[0]["score"]), 0.95)


def test_visual_pair_generation_checks_for_cancellation() -> None:
    feature: dict[str, Any] = {
        "image_id": 1,
        "pixel_digest": "one",
        "phash_parts": [0, 0, 0],
        "color_hist": [1.0],
        "hsv_hist": [1.0],
    }

    def cancel() -> None:
        raise ScanCancelled("cancelled")

    with pytest.raises(ScanCancelled):
        _visual_pairs([feature], 12, 0.88, cancel)


def test_visual_verifier_accepts_resized_structured_image(tmp_path: Path) -> None:
    first = tmp_path / "first.png"
    second = tmp_path / "second.jpg"
    make_pattern_image(first, (128, 128))
    Image.open(first).resize((80, 80), Image.Resampling.BILINEAR).save(second, quality=88)
    left = extract_image_features(first)
    right = extract_image_features(second)
    profile = verification_profile(
        Thresholds(clip_enabled=False, histogram_similarity=0.86, precision_mode="balanced")
    )

    meta = _verify_visual_pair(left, right, {"score": 0.9, "reason": "visual-hash"}, profile, "balanced")

    assert meta is not None
    assert meta["evidence"]["verified"] is True
    assert meta["evidence"]["layout_similarity"] >= 0.82


def test_visual_verifier_rejects_same_palette_different_layout(tmp_path: Path) -> None:
    first = tmp_path / "split-vertical.png"
    second = tmp_path / "split-horizontal.png"
    vertical = Image.new("RGB", (96, 96), (20, 80, 190))
    ImageDraw.Draw(vertical).rectangle((48, 0, 95, 95), fill=(220, 70, 55))
    horizontal = Image.new("RGB", (96, 96), (20, 80, 190))
    ImageDraw.Draw(horizontal).rectangle((0, 48, 95, 95), fill=(220, 70, 55))
    vertical.save(first)
    horizontal.save(second)
    profile = verification_profile(Thresholds(clip_enabled=False))

    meta = _verify_visual_pair(
        extract_image_features(first),
        extract_image_features(second),
        {"score": 0.99, "reason": "visual-hash"},
        profile,
        "conservative",
    )

    assert meta is None


def test_visual_verifier_rejects_flat_one_pixel_difference(tmp_path: Path) -> None:
    first = tmp_path / "flat.png"
    second = tmp_path / "flat-one-pixel.png"
    Image.new("RGB", (96, 96), (210, 20, 20)).save(first)
    edited = Image.new("RGB", (96, 96), (210, 20, 20))
    edited.putpixel((48, 48), (20, 20, 210))
    edited.save(second)
    profile = verification_profile(Thresholds(clip_enabled=False))

    meta = _verify_visual_pair(
        extract_image_features(first),
        extract_image_features(second),
        {"score": 1.0, "reason": "visual-hash"},
        profile,
        "conservative",
    )

    assert meta is None


def test_visual_verifier_rejects_different_icons(tmp_path: Path) -> None:
    circle_path = tmp_path / "circle.png"
    square_path = tmp_path / "square.png"
    circle = Image.new("RGB", (128, 128), (242, 242, 238))
    ImageDraw.Draw(circle).ellipse((34, 34, 94, 94), fill=(42, 126, 190))
    square = Image.new("RGB", (128, 128), (242, 242, 238))
    ImageDraw.Draw(square).rectangle((34, 34, 94, 94), fill=(42, 126, 190))
    circle.save(circle_path)
    square.save(square_path)
    profile = verification_profile(Thresholds(clip_enabled=False))

    meta = _verify_visual_pair(
        extract_image_features(circle_path),
        extract_image_features(square_path),
        {"score": 0.99, "reason": "visual-hash"},
        profile,
        "conservative",
    )

    assert meta is None


def test_low_information_visual_pair_requires_near_exact_layout(tmp_path: Path) -> None:
    first = tmp_path / "solid-large.png"
    second = tmp_path / "solid-small.png"
    make_image(first, (140, 140, 140), (96, 96))
    make_image(second, (140, 140, 140), (64, 64))
    profile = verification_profile(Thresholds(clip_enabled=False))

    meta = _verify_visual_pair(
        extract_image_features(first),
        extract_image_features(second),
        {"score": 1.0, "reason": "visual-hash"},
        profile,
        "conservative",
    )

    assert meta is None


def test_lightweight_low_information_visual_pair_requires_near_exact_layout(
    tmp_path: Path,
) -> None:
    first = tmp_path / "solid-large.png"
    second = tmp_path / "solid-small.png"
    make_image(first, (140, 140, 140), (96, 96))
    make_image(second, (140, 140, 140), (64, 64))
    left = extract_lightweight_image_features(first)
    right = extract_lightweight_image_features(second)
    left["image_id"] = 1
    right["image_id"] = 2

    verified = verify_candidate_pairs(
        {(1, 2): {"score": 1.0, "reason": "visual-hash"}},
        [left, right],
        Thresholds(clip_enabled=False, performance_profile="lightweight"),
    )

    assert verified == {}


def test_exact_pairs_bypass_visual_verifier() -> None:
    pairs: dict[tuple[int, int], dict[str, Any]] = {
        (1, 2): {"score": 1.0, "reason": "exact-pixel"}
    }

    verified = verify_candidate_pairs(
        pairs,
        [{"image_id": 1}, {"image_id": 2}],
        Thresholds(clip_enabled=False),
    )

    assert verified[(1, 2)]["reason"] == "exact-pixel"
    assert verified[(1, 2)]["evidence"]["match_type"] == "exact-pixel"


def test_clip_does_not_resurrect_verifier_rejected_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "images"
    root.mkdir()
    make_image(root / "solid-large.png", (140, 140, 140), (96, 96))
    make_image(root / "solid-small.png", (140, 140, 140), (64, 64))
    storage = Storage(tmp_path / "app.sqlite3")
    storage.init_schema()
    request = ScanRequest(path=str(root), thresholds=Thresholds(clip_enabled=True))
    scan_id = create_scan(storage, request)

    def forced_candidate_pairs(
        features: list[dict[str, Any]], *_: object, **__: object
    ) -> dict[tuple[int, int], dict[str, Any]]:
        return {
            (features[0]["image_id"], features[1]["image_id"]): {
                "score": 1.0,
                "reason": "visual-hash",
            }
        }

    def clip_filter(
        _scan_id: int,
        pairs: dict[tuple[int, int], dict[str, Any]],
        *_: object,
    ) -> dict[tuple[int, int], dict[str, Any]]:
        assert pairs == {}
        return pairs

    monkeypatch.setattr("app.services.scanner._candidate_pairs", forced_candidate_pairs)
    monkeypatch.setattr("app.services.scanner._apply_clip_filter", clip_filter)

    run_scan(scan_id, request, storage, "ViT-B-32", "laion2b_s34b_b79k", "cpu")

    scan = require_row(storage.get_scan(scan_id))
    assert scan["status"] == "completed"
    assert scan["duplicate_groups"] == 0


def test_strict_clip_mode_fails_on_image_preprocessing_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "images"
    root.mkdir()
    first_path = root / "a.png"
    second_path = root / "b.png"
    make_image(first_path, (1, 2, 3), (20, 20))
    make_image(second_path, (1, 2, 4), (20, 20))
    storage = Storage(tmp_path / "app.sqlite3")
    storage.init_schema()
    request = ScanRequest(path=str(root), thresholds=Thresholds(clip_enabled=True))
    scan_id = create_scan(storage, request)
    features: list[dict[str, Any]] = []
    for path in (first_path, second_path):
        feature = extract_image_features(path)
        cache_id, snapshot_id = storage.save_scan_image(scan_id, feature)
        feature["cache_image_id"] = cache_id
        feature["image_id"] = snapshot_id
        features.append(feature)
    pairs: dict[tuple[int, int], dict[str, Any]] = {
        (features[0]["image_id"], features[1]["image_id"]): {
            "score": 0.9,
            "reason": "visual-hash",
        }
    }

    class FailingEmbedder:
        revision = "test-revision"

        def __init__(self, *_: object) -> None:
            pass

        def embed_images(self, paths: list[str], **_: object) -> EmbedResult:
            return EmbedResult(vectors={}, failures={path: "decode failed" for path in paths})

    monkeypatch.setattr("app.services.scanner.ClipEmbedder", FailingEmbedder)

    def test_revision(*_: object) -> str:
        return "test-revision"

    monkeypatch.setattr("app.services.scanner.clip_revision", test_revision)

    with pytest.raises(RuntimeError, match="CLIP preprocessing failed"):
        _apply_clip_filter(
            scan_id,
            pairs,
            features,
            storage,
            "model",
            "pretrained",
            "cpu",
            0.9,
            lambda: None,
            False,
        )

    failures, total = storage.list_failures(scan_id, 10, 0)
    assert total == 2
    assert {failure["stage"] for failure in failures} == {"clip"}


def test_cached_clip_embedding_is_rejected_if_file_identity_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "images"
    root.mkdir()
    first_path = root / "a.png"
    second_path = root / "b.png"
    make_image(first_path, (1, 2, 3), (20, 20))
    make_image(second_path, (1, 2, 4), (20, 20))
    storage = Storage(tmp_path / "app.sqlite3")
    storage.init_schema()
    request = ScanRequest(path=str(root), thresholds=Thresholds(clip_enabled=True))
    scan_id = create_scan(storage, request)
    features: list[dict[str, Any]] = []
    for path in (first_path, second_path):
        feature = extract_image_features(path)
        cache_id, snapshot_id = storage.save_scan_image(scan_id, feature)
        storage.set_embedding(cache_id, "model", "pretrained", "test-revision", [1.0, 0.0])
        feature["cache_image_id"] = cache_id
        feature["image_id"] = snapshot_id
        features.append(feature)
    pairs: dict[tuple[int, int], dict[str, Any]] = {
        (features[0]["image_id"], features[1]["image_id"]): {
            "score": 0.9,
            "reason": "visual-hash",
        }
    }
    original_identity = get_file_identity
    changed_identity = replace(get_file_identity(first_path), mtime_ns=0)

    def identity_for_path(path: Path) -> FileIdentity:
        if Path(path) == first_path.resolve():
            return changed_identity
        return original_identity(Path(path))

    def test_revision(*_: object) -> str:
        return "test-revision"

    monkeypatch.setattr("app.services.scanner.clip_revision", test_revision)
    monkeypatch.setattr("app.services.scanner.get_file_identity", identity_for_path)

    filtered = _apply_clip_filter(
        scan_id,
        pairs,
        features,
        storage,
        "model",
        "pretrained",
        "cpu",
        0.9,
        lambda: None,
        True,
    )

    assert filtered == {}
    failures, total = storage.list_failures(scan_id, 10, 0)
    assert total == 1
    assert failures[0]["stage"] == "clip"


def test_clip_filter_reads_cached_embeddings_through_bulk_storage_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "images"
    root.mkdir()
    paths = [root / "a.png", root / "b.png"]
    make_image(paths[0], (1, 2, 3), (20, 20))
    make_image(paths[1], (1, 2, 4), (20, 20))
    storage = Storage(tmp_path / "app.sqlite3")
    storage.init_schema()
    request = ScanRequest(path=str(root), thresholds=Thresholds(clip_enabled=True))
    scan_id = create_scan(storage, request)
    features: list[dict[str, Any]] = []
    cache_ids: list[int] = []
    for path in paths:
        feature = extract_image_features(path)
        cache_id, snapshot_id = storage.save_scan_image(scan_id, feature)
        feature["cache_image_id"] = cache_id
        feature["image_id"] = snapshot_id
        cache_ids.append(cache_id)
        features.append(feature)
    storage.set_embeddings_bulk(
        "model",
        "pretrained",
        "test-revision",
        [(cache_id, [1.0, 0.0]) for cache_id in cache_ids],
    )
    pairs: dict[tuple[int, int], dict[str, Any]] = {
        (features[0]["image_id"], features[1]["image_id"]): {
            "score": 0.9,
            "reason": "visual-hash",
        }
    }

    def legacy_single_item_path(*_: object) -> None:
        raise AssertionError("single-item embedding storage path was used")

    monkeypatch.setattr(storage, "get_image", legacy_single_item_path)
    monkeypatch.setattr(storage, "set_embedding", legacy_single_item_path)
    monkeypatch.setattr(
        "app.services.scanner.clip_revision",
        lambda *_: "test-revision",
    )

    filtered = _apply_clip_filter(
        scan_id,
        pairs,
        features,
        storage,
        "model",
        "pretrained",
        "cpu",
        0.9,
        lambda: None,
        False,
    )

    assert set(filtered) == set(pairs)
    assert filtered[next(iter(pairs))]["clip_similarity"] == pytest.approx(1.0)


def test_cancel_requested_before_run_marks_scan_cancelled(tmp_path: Path) -> None:
    root = tmp_path / "images"
    root.mkdir()
    make_image(root / "a.png", (1, 2, 3), (20, 20))
    storage = Storage(tmp_path / "app.sqlite3")
    storage.init_schema()
    request = ScanRequest(path=str(root), thresholds=Thresholds(clip_enabled=False))
    scan_id = create_scan(storage, request)
    storage.request_cancel(scan_id)

    run_scan(scan_id, request, storage, "ViT-B-32", "laion2b_s34b_b79k", "cpu")

    assert require_row(storage.get_scan(scan_id))["status"] == "cancelled"


def test_scan_manager_sets_memory_cancel_before_requesting_database_cancel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = Storage(tmp_path / "app.sqlite3")
    storage.init_schema()
    scan_id = storage.create_scan(str(tmp_path), True, "model", "pretrained", "cpu", {})
    manager = ScanManager(storage)
    cancel_event = threading.Event()
    with manager._lock:
        manager._cancel_events[scan_id] = cancel_event
    event_states: list[bool] = []

    def request_cancel(_scan_id: int) -> bool:
        event_states.append(cancel_event.is_set())
        return True

    monkeypatch.setattr(storage, "request_cancel", request_cancel)

    assert manager.cancel(scan_id)
    manager.shutdown()

    assert event_states == [True]


def test_scan_manager_can_start_after_shutdown(tmp_path: Path) -> None:
    root = tmp_path / "images"
    root.mkdir()
    make_image(root / "a.png", (1, 2, 3), (20, 20))
    storage = Storage(tmp_path / "app.sqlite3")
    storage.init_schema()
    manager = ScanManager(storage)
    manager.shutdown()
    request = ScanRequest(path=str(root), device="cpu", thresholds=Thresholds(clip_enabled=False))

    scan_id = manager.start(request)
    with manager._lock:
        future = manager._futures.get(scan_id)
    if future is not None:
        future.result(timeout=5)
    manager.shutdown()

    assert require_row(storage.get_scan(scan_id))["status"] == "completed"


def test_scan_manager_cleanup_marks_unexpected_future_exception_failed(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "app.sqlite3")
    storage.init_schema()
    scan_id = storage.create_scan(str(tmp_path), True, "model", "pretrained", "cpu", {})
    manager = ScanManager(storage)
    future: Future[Any] = Future()
    with manager._lock:
        manager._futures[scan_id] = future
        manager._cancel_events[scan_id] = threading.Event()
    future.set_exception(RuntimeError("worker escaped"))

    manager._cleanup(scan_id, future)
    manager.shutdown()

    scan = require_row(storage.get_scan(scan_id))
    assert scan["status"] == "failed"
    assert scan["phase"] == "failed"
    assert scan["error"] == "worker escaped"
    assert scan["progress_detail"] == "Scan failed unexpectedly"

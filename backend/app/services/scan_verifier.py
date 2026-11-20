from __future__ import annotations

from typing import Any

from app.models.schemas import PrecisionMode, Thresholds
from app.services.features import (
    dhash_distance,
    edge_similarity,
    histogram_similarity,
    layout_similarity,
    mean_color_similarity,
    phash_distance,
    structural_similarity,
    whash_distance,
)
from app.services.scan_profiles import (
    PRECISION_PROFILE_DEFAULTS,
    SMALL_DIFFERENCE_MAX_ASPECT_DELTA,
    SMALL_DIFFERENCE_MAX_PIXEL_DELTA,
    SMALL_DIFFERENCE_MIN_EDGE,
    SMALL_DIFFERENCE_MIN_HISTOGRAM,
    SMALL_DIFFERENCE_MIN_LAYOUT,
    SMALL_DIFFERENCE_MIN_STRUCTURE,
    SMALL_DIFFERENCE_PHASH_SLACK,
    STRUCTURE_OVERRIDE_MIN_MEAN_COLOR,
    STRUCTURE_OVERRIDE_MIN_SIMILARITY,
    ULTRA_STRICT_MAX_PHASH_DISTANCE,
    ULTRA_STRICT_MIN_CLIP,
    ULTRA_STRICT_MIN_HISTOGRAM,
    ProfileDefaults,
    VerificationProfile,
)

PairKey = tuple[int, int]
PairMeta = dict[str, Any]
PairMap = dict[PairKey, PairMeta]

__all__ = [
    "calculate_aspect_delta",
    "calculate_pixel_count_delta",
    "effective_thresholds",
    "small_difference_pass",
    "small_difference_score",
    "structure_override_pass",
    "verification_profile",
    "verify_candidate_pairs",
]


def effective_thresholds(thresholds: Thresholds) -> Thresholds:
    effective = thresholds.model_copy(deep=True)
    if effective.performance_profile == "lightweight":
        effective.clip_enabled = False
    if effective.precision_mode == "exact_pixel":
        effective.phash_distance = 0
        effective.histogram_similarity = 1.0
        effective.clip_similarity = 1.0
        effective.clip_enabled = False
    elif effective.precision_mode == "ultra_strict":
        effective.phash_distance = min(
            int(effective.phash_distance),
            ULTRA_STRICT_MAX_PHASH_DISTANCE,
        )
        effective.histogram_similarity = max(
            float(effective.histogram_similarity),
            ULTRA_STRICT_MIN_HISTOGRAM,
        )
        effective.clip_similarity = max(float(effective.clip_similarity), ULTRA_STRICT_MIN_CLIP)
    return effective


def verify_candidate_pairs(
    pairs: PairMap,
    features: list[dict[str, Any]],
    thresholds: Thresholds,
    check_cancelled: Any | None = None,
) -> PairMap:
    feature_by_id = {int(feature["image_id"]): feature for feature in features}
    verified: PairMap = {}
    profile = verification_profile(thresholds)
    for index, (pair, meta) in enumerate(pairs.items()):
        if check_cancelled and index % 256 == 0:
            check_cancelled()
        if meta["reason"] == "exact-pixel":
            verified[pair] = {
                **meta,
                "evidence": {
                    "verified": True,
                    "match_type": "exact-pixel",
                    "performance_profile": thresholds.performance_profile,
                },
            }
            continue
        if thresholds.precision_mode == "exact_pixel":
            continue
        left = feature_by_id.get(pair[0])
        right = feature_by_id.get(pair[1])
        if left is None or right is None:
            continue
        if thresholds.performance_profile == "lightweight":
            next_meta = _verify_lightweight_pair(left, right, meta, thresholds.precision_mode)
        else:
            next_meta = _verify_visual_pair(
                left,
                right,
                meta,
                profile,
                thresholds.precision_mode,
                thresholds.performance_profile,
            )
        if next_meta is not None:
            verified[pair] = next_meta
    return verified


def verification_profile(thresholds: Thresholds) -> VerificationProfile:
    thresholds = effective_thresholds(thresholds)
    mode = thresholds.precision_mode
    values: ProfileDefaults = dict(PRECISION_PROFILE_DEFAULTS[mode])
    if mode in {"ultra_strict", "conservative"}:
        values["max_phash_distance"] = min(
            int(thresholds.phash_distance), int(values["max_phash_distance"])
        )
        values["min_histogram_similarity"] = max(
            float(thresholds.histogram_similarity), float(values["min_histogram_similarity"])
        )
    elif mode == "broad":
        values["max_phash_distance"] = max(
            int(thresholds.phash_distance), int(values["max_phash_distance"])
        )
        values["min_histogram_similarity"] = min(
            float(thresholds.histogram_similarity), float(values["min_histogram_similarity"])
        )
    else:
        values["max_phash_distance"] = int(thresholds.phash_distance)
        values["min_histogram_similarity"] = float(thresholds.histogram_similarity)
    return VerificationProfile(
        max_phash_distance=int(values["max_phash_distance"]),
        max_dhash_distance=int(values["max_dhash_distance"]),
        max_whash_distance=int(values["max_whash_distance"]),
        min_histogram_similarity=float(values["min_histogram_similarity"]),
        min_layout_similarity=float(values["min_layout_similarity"]),
        min_structural_similarity=float(values["min_structural_similarity"]),
        min_edge_similarity=float(values["min_edge_similarity"]),
        min_quality_score=float(values["min_quality_score"]),
        max_aspect_delta=float(values["max_aspect_delta"]),
        min_support_signals=int(values["min_support_signals"]),
    )


def _verify_visual_pair(
    left: dict[str, Any],
    right: dict[str, Any],
    meta: PairMeta,
    profile: VerificationProfile,
    precision_mode: PrecisionMode,
    performance_profile: str = "standard",
) -> PairMeta | None:
    p_distance = (
        int(meta["phash_distance"]) if "phash_distance" in meta else phash_distance(left, right)
    )
    d_distance = int(meta["dhash_distance"]) if "dhash_distance" in meta else dhash_distance(left, right)
    w_distance = int(meta["whash_distance"]) if "whash_distance" in meta else whash_distance(left, right)
    hist_score = (
        float(meta["histogram_similarity"])
        if "histogram_similarity" in meta
        else histogram_similarity(left, right)
    )
    mean_color_score = (
        float(meta["mean_color_similarity"])
        if "mean_color_similarity" in meta
        else mean_color_similarity(left, right)
    )
    layout_score = (
        float(meta["layout_similarity"])
        if "layout_similarity" in meta
        else layout_similarity(left, right)
    )
    structure_score = (
        float(meta["structural_similarity"])
        if "structural_similarity" in meta
        else structural_similarity(left, right)
    )
    edge_score = (
        float(meta["edge_similarity"])
        if "edge_similarity" in meta
        else edge_similarity(left, right)
    )
    min_quality = (
        float(meta["quality_score"])
        if "quality_score" in meta
        else min(float(left.get("quality_score", 0.0)), float(right.get("quality_score", 0.0)))
    )
    aspect_delta = (
        float(meta["aspect_delta"])
        if "aspect_delta" in meta
        else calculate_aspect_delta(left, right)
    )
    pixel_count_delta = (
        float(meta["pixel_count_delta"])
        if "pixel_count_delta" in meta
        else calculate_pixel_count_delta(left, right)
    )
    low_information = min_quality < profile.min_quality_score

    evidence: PairMeta = {
        "verified": False,
        "precision_mode": precision_mode,
        "performance_profile": performance_profile,
        "phash_distance": p_distance,
        "dhash_distance": d_distance,
        "whash_distance": w_distance,
        "histogram_similarity": round(hist_score, 4),
        "layout_similarity": round(layout_score, 4),
        "structural_similarity": round(structure_score, 4),
        "edge_similarity": round(edge_score, 4),
        "quality_score": round(min_quality, 4),
        "aspect_delta": round(aspect_delta, 4),
        "low_information": low_information,
    }

    if low_information:
        low_info_pass = (
            int(left["width"]) == int(right["width"])
            and int(left["height"]) == int(right["height"])
            and aspect_delta <= min(profile.max_aspect_delta, 0.02)
            and p_distance <= 1
            and d_distance <= 1
            and w_distance <= 2
            and hist_score >= 0.995
            and layout_score >= 0.995
            and structure_score >= max(profile.min_structural_similarity, 0.995)
        )
        if not low_info_pass:
            return None
        evidence["verified"] = True
        evidence["support_signals"] = 3
        score = min(float(meta["score"]), 0.90)
        return {**meta, "score": score, "evidence": evidence}

    support_signals = sum(
        (
            d_distance <= profile.max_dhash_distance,
            w_distance <= profile.max_whash_distance,
            edge_score >= profile.min_edge_similarity,
        )
    )
    structure_override = structure_override_pass(
        p_distance,
        mean_color_score,
        layout_score,
        structure_score,
        min_quality,
        aspect_delta,
        support_signals,
        profile,
    )
    required_pass = structure_override or (
        aspect_delta <= profile.max_aspect_delta
        and p_distance <= profile.max_phash_distance
        and hist_score >= profile.min_histogram_similarity
        and layout_score >= profile.min_layout_similarity
        and structure_score >= profile.min_structural_similarity
        and min_quality >= profile.min_quality_score
    )
    evidence["support_signals"] = support_signals
    if not required_pass or support_signals < profile.min_support_signals:
        if small_difference_pass(
            p_distance,
            d_distance,
            w_distance,
            hist_score,
            layout_score,
            structure_score,
            edge_score,
            min_quality,
            aspect_delta,
            pixel_count_delta,
            profile,
        ):
            evidence["verified"] = True
            evidence["match_type"] = "small-difference"
            evidence["small_difference"] = True
            evidence["pixel_count_delta"] = round(pixel_count_delta, 4)
            score = small_difference_score(
                p_distance,
                d_distance,
                w_distance,
                hist_score,
                layout_score,
                edge_score,
            )
            return {
                **meta,
                "score": score,
                "reason": "small-difference",
                "phash_distance": p_distance,
                "histogram_similarity": hist_score,
                "dhash_distance": d_distance,
                "whash_distance": w_distance,
                "layout_similarity": layout_score,
                "structural_similarity": structure_score,
                "edge_similarity": edge_score,
                "quality_score": min_quality,
                "evidence": evidence,
            }
        return None

    if structure_override:
        evidence["match_type"] = "structure-preserving"

    phash_score = 1.0 - (p_distance / 64.0)
    dhash_score = 1.0 - (d_distance / 64.0)
    whash_score = 1.0 - (w_distance / 64.0)
    score = (
        phash_score * 0.24
        + dhash_score * 0.16
        + whash_score * 0.16
        + hist_score * 0.18
        + layout_score * 0.20
        + edge_score * 0.06
    )
    evidence["verified"] = True
    return {
        **meta,
        "score": max(0.0, min(1.0, score)),
        "phash_distance": p_distance,
        "histogram_similarity": hist_score,
        "dhash_distance": d_distance,
        "whash_distance": w_distance,
        "layout_similarity": layout_score,
        "structural_similarity": structure_score,
        "edge_similarity": edge_score,
        "quality_score": min_quality,
        "evidence": evidence,
    }


def _verify_lightweight_pair(
    left: dict[str, Any],
    right: dict[str, Any],
    meta: PairMeta,
    precision_mode: PrecisionMode,
) -> PairMeta | None:
    p_distance = (
        int(meta["phash_distance"]) if "phash_distance" in meta else phash_distance(left, right)
    )
    hist_score = (
        float(meta["histogram_similarity"])
        if "histogram_similarity" in meta
        else histogram_similarity(left, right)
    )
    layout_score = (
        float(meta["layout_similarity"])
        if "layout_similarity" in meta
        else layout_similarity(left, right)
    )
    aspect_delta = (
        float(meta["aspect_delta"])
        if "aspect_delta" in meta
        else calculate_aspect_delta(left, right)
    )
    min_quality = (
        float(meta["quality_score"])
        if "quality_score" in meta
        else min(float(left.get("quality_score", 0.0)), float(right.get("quality_score", 0.0)))
    )
    profile_defaults = PRECISION_PROFILE_DEFAULTS[precision_mode]
    low_information = min_quality < float(profile_defaults["min_quality_score"])
    if low_information:
        d_distance = (
            int(meta["dhash_distance"]) if "dhash_distance" in meta else dhash_distance(left, right)
        )
        w_distance = (
            int(meta["whash_distance"]) if "whash_distance" in meta else whash_distance(left, right)
        )
        low_info_pass = (
            int(left["width"]) == int(right["width"])
            and int(left["height"]) == int(right["height"])
            and aspect_delta <= min(float(profile_defaults["max_aspect_delta"]), 0.02)
            and p_distance <= 1
            and d_distance <= 1
            and w_distance <= 2
            and hist_score >= 0.995
            and layout_score >= 0.995
        )
        if not low_info_pass:
            return None
    if p_distance > 18 or hist_score < 0.72 or layout_score < 0.60 or aspect_delta > 0.25:
        return None

    phash_score = 1.0 - (p_distance / 64.0)
    score = max(0.0, min(0.95, (phash_score * 0.55) + (hist_score * 0.30) + (layout_score * 0.15)))
    evidence: PairMeta = {
        "verified": True,
        "precision_mode": precision_mode,
        "performance_profile": "lightweight",
        "reduced_evidence": True,
        "candidate_limited": bool(meta.get("candidate_limited")),
        "phash_distance": p_distance,
        "histogram_similarity": round(hist_score, 4),
        "layout_similarity": round(layout_score, 4),
        "aspect_delta": round(aspect_delta, 4),
        "quality_score": round(min_quality, 4),
        "low_information": low_information,
    }
    return {
        **meta,
        "score": score,
        "reason": "visual-hash",
        "phash_distance": p_distance,
        "histogram_similarity": hist_score,
        "layout_similarity": layout_score,
        "evidence": evidence,
    }


def calculate_aspect_delta(left: dict[str, Any], right: dict[str, Any]) -> float:
    left_ratio = float(left["width"]) / max(1.0, float(left["height"]))
    right_ratio = float(right["width"]) / max(1.0, float(right["height"]))
    return abs(left_ratio - right_ratio) / max(left_ratio, right_ratio, 1e-9)


def calculate_pixel_count_delta(left: dict[str, Any], right: dict[str, Any]) -> float:
    left_pixels = max(1, int(left["width"]) * int(left["height"]))
    right_pixels = max(1, int(right["width"]) * int(right["height"]))
    return abs(left_pixels - right_pixels) / max(left_pixels, right_pixels)


def structure_override_pass(
    p_distance: int,
    mean_color_score: float,
    layout_score: float,
    structure_score: float,
    min_quality: float,
    aspect_delta: float,
    support_signals: int,
    profile: VerificationProfile,
) -> bool:
    if profile.min_structural_similarity >= 0.99:
        return False
    return (
        aspect_delta <= profile.max_aspect_delta
        and p_distance <= profile.max_phash_distance
        and mean_color_score >= STRUCTURE_OVERRIDE_MIN_MEAN_COLOR
        and layout_score >= profile.min_layout_similarity
        and structure_score
        >= max(
            profile.min_structural_similarity,
            STRUCTURE_OVERRIDE_MIN_SIMILARITY,
        )
        and min_quality >= profile.min_quality_score
        and support_signals >= profile.min_support_signals
    )


def small_difference_pass(
    p_distance: int,
    d_distance: int,
    w_distance: int,
    hist_score: float,
    layout_score: float,
    structure_score: float,
    edge_score: float,
    min_quality: float,
    aspect_delta: float,
    pixel_delta: float,
    profile: VerificationProfile,
) -> bool:
    return (
        min_quality >= profile.min_quality_score
        and pixel_delta <= SMALL_DIFFERENCE_MAX_PIXEL_DELTA
        and aspect_delta <= min(profile.max_aspect_delta, SMALL_DIFFERENCE_MAX_ASPECT_DELTA)
        and p_distance <= min(64, profile.max_phash_distance + SMALL_DIFFERENCE_PHASH_SLACK)
        and d_distance <= profile.max_dhash_distance
        and w_distance <= profile.max_whash_distance
        and hist_score >= max(profile.min_histogram_similarity, SMALL_DIFFERENCE_MIN_HISTOGRAM)
        and layout_score >= _small_difference_min_layout(profile)
        and structure_score >= _small_difference_min_structure(profile)
        and edge_score >= max(profile.min_edge_similarity, SMALL_DIFFERENCE_MIN_EDGE)
    )


def _small_difference_min_layout(profile: VerificationProfile) -> float:
    if profile.min_layout_similarity >= 0.99:
        return profile.min_layout_similarity
    return SMALL_DIFFERENCE_MIN_LAYOUT


def _small_difference_min_structure(profile: VerificationProfile) -> float:
    if profile.min_structural_similarity >= 0.99:
        return profile.min_structural_similarity
    return SMALL_DIFFERENCE_MIN_STRUCTURE


def small_difference_score(
    p_distance: int,
    d_distance: int,
    w_distance: int,
    hist_score: float,
    layout_score: float,
    edge_score: float,
) -> float:
    phash_score = 1.0 - (p_distance / 64.0)
    dhash_score = 1.0 - (d_distance / 64.0)
    whash_score = 1.0 - (w_distance / 64.0)
    score = (
        phash_score * 0.14
        + dhash_score * 0.12
        + whash_score * 0.12
        + hist_score * 0.24
        + layout_score * 0.25
        + edge_score * 0.13
    )
    return max(0.0, min(0.97, score))

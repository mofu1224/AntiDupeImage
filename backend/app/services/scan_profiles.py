from __future__ import annotations

from dataclasses import dataclass

from app.models.schemas import PrecisionMode


@dataclass(frozen=True)
class VerificationProfile:
    max_phash_distance: int
    max_dhash_distance: int
    max_whash_distance: int
    min_histogram_similarity: float
    min_layout_similarity: float
    min_structural_similarity: float
    min_edge_similarity: float
    min_quality_score: float
    max_aspect_delta: float
    min_support_signals: int


ProfileDefaults = dict[str, int | float]
ThresholdPreset = dict[str, int | float]


SMALL_DIFFERENCE_PHASH_SLACK = 4
SMALL_DIFFERENCE_MIN_HISTOGRAM = 0.98
SMALL_DIFFERENCE_MIN_LAYOUT = 0.86
SMALL_DIFFERENCE_MIN_STRUCTURE = 0.93
SMALL_DIFFERENCE_MIN_EDGE = 0.90
SMALL_DIFFERENCE_MAX_ASPECT_DELTA = 0.02
SMALL_DIFFERENCE_MAX_PIXEL_DELTA = 0.01
STRUCTURE_OVERRIDE_MIN_MEAN_COLOR = 0.90
STRUCTURE_OVERRIDE_MIN_SIMILARITY = 0.985
ULTRA_STRICT_MAX_PHASH_DISTANCE = 2
ULTRA_STRICT_MIN_HISTOGRAM = 0.99
ULTRA_STRICT_MIN_CLIP = 0.99


PRECISION_THRESHOLD_PRESETS: dict[PrecisionMode, ThresholdPreset] = {
    "exact_pixel": {
        "phash_distance": 0,
        "histogram_similarity": 1.0,
        "clip_similarity": 1.0,
    },
    "ultra_strict": {
        "phash_distance": 2,
        "histogram_similarity": 0.99,
        "clip_similarity": 0.99,
    },
    "conservative": {
        "phash_distance": 10,
        "histogram_similarity": 0.90,
        "clip_similarity": 0.92,
    },
    "balanced": {
        "phash_distance": 12,
        "histogram_similarity": 0.86,
        "clip_similarity": 0.92,
    },
    "broad": {
        "phash_distance": 16,
        "histogram_similarity": 0.78,
        "clip_similarity": 0.88,
    },
}


PRECISION_PROFILE_DEFAULTS: dict[PrecisionMode, ProfileDefaults] = {
    "exact_pixel": {
        "max_phash_distance": 0,
        "max_dhash_distance": 0,
        "max_whash_distance": 0,
        "min_histogram_similarity": 1.0,
        "min_layout_similarity": 1.0,
        "min_structural_similarity": 1.0,
        "min_edge_similarity": 1.0,
        "min_quality_score": 0.0,
        "max_aspect_delta": 0.0,
        "min_support_signals": 3,
    },
    "ultra_strict": {
        "max_phash_distance": ULTRA_STRICT_MAX_PHASH_DISTANCE,
        "max_dhash_distance": 2,
        "max_whash_distance": 2,
        "min_histogram_similarity": ULTRA_STRICT_MIN_HISTOGRAM,
        "min_layout_similarity": 0.99,
        "min_structural_similarity": 0.995,
        "min_edge_similarity": 0.98,
        "min_quality_score": 0.20,
        "max_aspect_delta": 0.005,
        "min_support_signals": 3,
    },
    "conservative": {
        "max_phash_distance": 10,
        "max_dhash_distance": 10,
        "max_whash_distance": 12,
        "min_histogram_similarity": 0.90,
        "min_layout_similarity": 0.88,
        "min_structural_similarity": 0.95,
        "min_edge_similarity": 0.78,
        "min_quality_score": 0.10,
        "max_aspect_delta": 0.03,
        "min_support_signals": 2,
    },
    "balanced": {
        "max_phash_distance": 12,
        "max_dhash_distance": 16,
        "max_whash_distance": 18,
        "min_histogram_similarity": 0.86,
        "min_layout_similarity": 0.82,
        "min_structural_similarity": 0.95,
        "min_edge_similarity": 0.70,
        "min_quality_score": 0.07,
        "max_aspect_delta": 0.08,
        "min_support_signals": 1,
    },
    "broad": {
        "max_phash_distance": 16,
        "max_dhash_distance": 22,
        "max_whash_distance": 24,
        "min_histogram_similarity": 0.78,
        "min_layout_similarity": 0.72,
        "min_structural_similarity": 0.90,
        "min_edge_similarity": 0.58,
        "min_quality_score": 0.04,
        "max_aspect_delta": 0.20,
        "min_support_signals": 1,
    },
}

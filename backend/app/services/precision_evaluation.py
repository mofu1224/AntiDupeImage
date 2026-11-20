from __future__ import annotations

from dataclasses import asdict, dataclass
from itertools import combinations
from pathlib import Path
from typing import Any, Iterable


PathPair = tuple[str, str]


@dataclass(frozen=True)
class PrecisionMetrics:
    true_positives: int
    false_positives: int
    false_negatives: int
    predicted_pairs: int
    expected_pairs: int
    impure_groups: int
    precision: float
    recall: float
    f1: float

    def as_dict(self) -> dict[str, int | float]:
        return asdict(self)


def evaluate_scan_groups(
    groups: list[dict[str, Any]],
    positive_pairs: Iterable[PathPair],
    negative_pairs: Iterable[PathPair],
) -> PrecisionMetrics:
    positives = {_normalize_pair(pair) for pair in positive_pairs}
    negatives = {_normalize_pair(pair) for pair in negative_pairs}
    predicted = direct_group_pairs(groups)
    true_positives = len(predicted & positives)
    false_positives = len(predicted - positives)
    false_negatives = len(positives - predicted)
    precision = (
        true_positives / (true_positives + false_positives)
        if true_positives + false_positives
        else 1.0
    )
    recall = true_positives / len(positives) if positives else 1.0
    f1 = (2.0 * precision * recall) / (precision + recall) if precision + recall else 0.0
    return PrecisionMetrics(
        true_positives=true_positives,
        false_positives=false_positives,
        false_negatives=false_negatives,
        predicted_pairs=len(predicted),
        expected_pairs=len(positives),
        impure_groups=_count_impure_groups(groups, negatives),
        precision=precision,
        recall=recall,
        f1=f1,
    )


def direct_group_pairs(groups: list[dict[str, Any]]) -> set[PathPair]:
    pairs: set[PathPair] = set()
    for group in groups:
        keep_id = int(group["recommended_keep_id"])
        images = group.get("images", [])
        keep = next((image for image in images if int(image["id"]) == keep_id), None)
        if keep is None:
            continue
        for image in images:
            if int(image["id"]) == keep_id:
                continue
            pairs.add(ordered_path_pair(str(keep["path"]), str(image["path"])))
    return pairs


def ordered_path_pair(left: str | Path, right: str | Path) -> PathPair:
    left_path = str(Path(left).resolve())
    right_path = str(Path(right).resolve())
    return (left_path, right_path) if left_path <= right_path else (right_path, left_path)


def _count_impure_groups(groups: list[dict[str, Any]], negatives: set[PathPair]) -> int:
    impure = 0
    for group in groups:
        paths = [str(image["path"]) for image in group.get("images", [])]
        group_pairs = {ordered_path_pair(left, right) for left, right in combinations(paths, 2)}
        if group_pairs & negatives:
            impure += 1
    return impure


def _normalize_pair(pair: PathPair) -> PathPair:
    if len(pair) != 2:
        raise ValueError("Precision label pairs must contain exactly two paths")
    return ordered_path_pair(pair[0], pair[1])

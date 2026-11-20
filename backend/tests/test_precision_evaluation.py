from __future__ import annotations

from pathlib import Path

from app.services.precision_evaluation import evaluate_scan_groups, ordered_path_pair


def image(image_id: int, path: Path) -> dict[str, int | str]:
    return {"id": image_id, "path": str(path)}


def test_evaluate_scan_groups_reports_direct_pair_metrics(tmp_path: Path) -> None:
    original = tmp_path / "original.png"
    duplicate = tmp_path / "duplicate.jpg"
    unrelated = tmp_path / "unrelated.png"
    groups = [
        {
            "recommended_keep_id": 1,
            "images": [image(1, original), image(2, duplicate)],
        }
    ]

    metrics = evaluate_scan_groups(
        groups,
        {ordered_path_pair(original, duplicate)},
        {
            ordered_path_pair(original, unrelated),
            ordered_path_pair(duplicate, unrelated),
        },
    )

    assert metrics.true_positives == 1
    assert metrics.false_positives == 0
    assert metrics.false_negatives == 0
    assert metrics.precision == 1.0
    assert metrics.recall == 1.0
    assert metrics.f1 == 1.0
    assert metrics.impure_groups == 0


def test_evaluate_scan_groups_counts_unknown_prediction_and_impure_group(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    groups = [
        {
            "recommended_keep_id": 1,
            "images": [image(1, first), image(2, second)],
        }
    ]
    negative = ordered_path_pair(first, second)

    metrics = evaluate_scan_groups(groups, set(), {negative})

    assert metrics.true_positives == 0
    assert metrics.false_positives == 1
    assert metrics.precision == 0.0
    assert metrics.impure_groups == 1

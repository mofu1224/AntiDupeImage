from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any

from app.core.db import Storage
from app.models.schemas import PrecisionMode, ScanRequest, Thresholds
from app.services.precision_evaluation import PathPair, evaluate_scan_groups, ordered_path_pair
from app.services.scan_profiles import PRECISION_THRESHOLD_PRESETS
from app.services.scanner import run_scan


DEFAULT_MODEL = "ViT-B-32"
DEFAULT_PRETRAINED = "laion2b_s34b_b79k"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate AntiDupeImage precision against a local labeled image corpus."
    )
    parser.add_argument(
        "--manifest",
        required=True,
        type=Path,
        help="JSON file containing root, positive_pairs, and negative_pairs.",
    )
    parser.add_argument(
        "--mode",
        choices=("exact_pixel", "ultra_strict", "conservative", "balanced", "broad"),
        default="conservative",
    )
    args = parser.parse_args()

    root, positive_pairs, negative_pairs = load_manifest(args.manifest)
    mode: PrecisionMode = args.mode
    preset = PRECISION_THRESHOLD_PRESETS[mode]
    thresholds = Thresholds(
        precision_mode=mode,
        phash_distance=int(preset["phash_distance"]),
        histogram_similarity=float(preset["histogram_similarity"]),
        clip_similarity=float(preset["clip_similarity"]),
        clip_enabled=False,
    )
    request = ScanRequest(
        path=str(root),
        recursive=True,
        device="cpu",
        thresholds=thresholds,
    )

    with tempfile.TemporaryDirectory(prefix="antidupeimage-precision-") as app_dir:
        storage = Storage(Path(app_dir) / "evaluation.sqlite3")
        storage.init_schema()
        scan_id = storage.create_scan(
            request.path,
            request.recursive,
            DEFAULT_MODEL,
            DEFAULT_PRETRAINED,
            "cpu",
            thresholds.model_dump(),
        )
        run_scan(
            scan_id,
            request,
            storage,
            DEFAULT_MODEL,
            DEFAULT_PRETRAINED,
            "cpu",
        )
        scan = storage.get_scan(scan_id)
        if scan is None or scan["status"] != "completed":
            error = scan.get("error") if scan else "scan row is missing"
            raise RuntimeError(f"Precision evaluation scan failed: {error}")
        groups = storage.get_groups(scan_id, max(1, storage.count_groups(scan_id)), 0)
        metrics = evaluate_scan_groups(groups, positive_pairs, negative_pairs)

    print(
        json.dumps(
            {
                "mode": mode,
                **metrics.as_dict(),
            },
            sort_keys=True,
        )
    )
    return 0


def load_manifest(path: Path) -> tuple[Path, set[PathPair], set[PathPair]]:
    manifest_path = path.expanduser().resolve(strict=True)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Precision manifest must be a JSON object")

    root_value = payload.get("root")
    if not isinstance(root_value, str) or not root_value.strip():
        raise ValueError("Precision manifest root must be a non-empty string")
    candidate_root = Path(root_value).expanduser()
    if not candidate_root.is_absolute():
        candidate_root = manifest_path.parent / candidate_root
    root = candidate_root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Precision manifest root must be a directory")

    positives = _load_pairs(payload, "positive_pairs", root)
    negatives = _load_pairs(payload, "negative_pairs", root)
    if positives & negatives:
        raise ValueError("A labeled pair cannot be both positive and negative")
    return root, positives, negatives


def _load_pairs(payload: dict[str, Any], key: str, root: Path) -> set[PathPair]:
    raw_pairs = payload.get(key)
    if not isinstance(raw_pairs, list):
        raise ValueError(f"Precision manifest {key} must be a list")
    pairs: set[PathPair] = set()
    for raw_pair in raw_pairs:
        if (
            not isinstance(raw_pair, list)
            or len(raw_pair) != 2
            or not all(isinstance(value, str) for value in raw_pair)
        ):
            raise ValueError(f"Every {key} item must contain two path strings")
        resolved = [_resolve_image_path(root, value) for value in raw_pair]
        if resolved[0] == resolved[1]:
            raise ValueError(f"{key} cannot label a file against itself")
        pairs.add(ordered_path_pair(resolved[0], resolved[1]))
    return pairs


def _resolve_image_path(root: Path, value: str) -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("Precision manifest paths must stay inside root") from exc
    if not resolved.is_file():
        raise ValueError("Precision manifest paths must reference files")
    return resolved


if __name__ == "__main__":
    raise SystemExit(main())

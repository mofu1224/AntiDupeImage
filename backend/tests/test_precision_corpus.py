from __future__ import annotations

import random
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from shutil import copyfile
from typing import Literal

import pytest

Image = pytest.importorskip("PIL.Image")
ImageDraw = pytest.importorskip("PIL.ImageDraw")
ImageEnhance = pytest.importorskip("PIL.ImageEnhance")

from app.core.db import Storage
from app.models.schemas import PrecisionMode, ScanRequest, Thresholds
from app.services.precision_evaluation import (
    PathPair,
    PrecisionMetrics,
    evaluate_scan_groups,
    ordered_path_pair,
)
from app.services.scan_profiles import PRECISION_THRESHOLD_PRESETS
from app.services.scanner import run_scan


CorpusSplit = Literal["calibration", "holdout"]


@dataclass(frozen=True)
class GeneratedCorpus:
    root: Path
    positive_pairs: set[PathPair]
    negative_pairs: set[PathPair]


@pytest.fixture(scope="module")
def calibration_corpus(tmp_path_factory: pytest.TempPathFactory) -> GeneratedCorpus:
    return build_precision_corpus(tmp_path_factory.mktemp("precision-calibration"), "calibration")


@pytest.fixture(scope="module")
def holdout_corpus(tmp_path_factory: pytest.TempPathFactory) -> GeneratedCorpus:
    return build_precision_corpus(tmp_path_factory.mktemp("precision-holdout"), "holdout")


@pytest.mark.parametrize(
    ("mode", "minimum_precision", "minimum_recall"),
    [
        ("conservative", 1.0, 0.90),
        ("balanced", 0.98, 0.95),
    ],
)
def test_calibration_precision_targets(
    tmp_path: Path,
    calibration_corpus: GeneratedCorpus,
    mode: PrecisionMode,
    minimum_precision: float,
    minimum_recall: float,
) -> None:
    metrics = scan_corpus(tmp_path, calibration_corpus, mode)

    assert metrics.precision >= minimum_precision
    assert metrics.recall >= minimum_recall
    assert metrics.impure_groups == 0


@pytest.mark.parametrize(
    ("mode", "minimum_precision", "minimum_recall"),
    [
        ("conservative", 1.0, 0.90),
        ("balanced", 0.98, 0.95),
    ],
)
def test_holdout_precision_targets(
    tmp_path: Path,
    holdout_corpus: GeneratedCorpus,
    mode: PrecisionMode,
    minimum_precision: float,
    minimum_recall: float,
) -> None:
    metrics = scan_corpus(tmp_path, holdout_corpus, mode)

    assert metrics.precision >= minimum_precision
    assert metrics.recall >= minimum_recall
    assert metrics.impure_groups == 0


def test_exact_pixel_corpus_rejects_reencoded_image(tmp_path: Path) -> None:
    root = tmp_path / "exact-corpus"
    root.mkdir()
    original = root / "original.png"
    exact_copy = root / "exact-copy.png"
    reencoded = root / "reencoded.jpg"
    make_scene(original, 9100)
    copyfile(original, exact_copy)
    Image.open(original).save(reencoded, quality=90)
    corpus = GeneratedCorpus(
        root=root,
        positive_pairs={ordered_path_pair(original, exact_copy)},
        negative_pairs={
            ordered_path_pair(original, reencoded),
            ordered_path_pair(exact_copy, reencoded),
        },
    )

    metrics = scan_corpus(tmp_path, corpus, "exact_pixel")

    assert metrics.precision == 1.0
    assert metrics.recall == 1.0
    assert metrics.false_positives == 0
    assert metrics.impure_groups == 0


def build_precision_corpus(root: Path, split: CorpusSplit) -> GeneratedCorpus:
    root.mkdir(parents=True, exist_ok=True)
    seed_base = 1000 if split == "calibration" else 7000
    transformations = ("jpeg", "webp", "resize", "tone", "overlay")
    positive_pairs: set[PathPair] = set()
    all_paths: list[Path] = []

    for index in range(20):
        kind = transformations[index % len(transformations)]
        original = root / f"positive-{index:02d}-original.png"
        suffix = ".jpg" if kind == "jpeg" else ".webp" if kind == "webp" else ".png"
        variant = root / f"positive-{index:02d}-{kind}{suffix}"
        make_scene(original, seed_base + index)
        make_positive_variant(original, variant, kind, seed_base + index)
        positive_pairs.add(ordered_path_pair(original, variant))
        all_paths.extend((original, variant))

    hard_negative_paths = make_hard_negatives(root, seed_base + 500)
    all_paths.extend(hard_negative_paths)
    all_pairs = {ordered_path_pair(left, right) for left, right in combinations(all_paths, 2)}
    return GeneratedCorpus(
        root=root,
        positive_pairs=positive_pairs,
        negative_pairs=all_pairs - positive_pairs,
    )


def scan_corpus(
    app_root: Path,
    corpus: GeneratedCorpus,
    mode: PrecisionMode,
) -> PrecisionMetrics:
    preset = PRECISION_THRESHOLD_PRESETS[mode]
    thresholds = Thresholds(
        precision_mode=mode,
        phash_distance=int(preset["phash_distance"]),
        histogram_similarity=float(preset["histogram_similarity"]),
        clip_similarity=float(preset["clip_similarity"]),
        clip_enabled=False,
    )
    request = ScanRequest(
        path=str(corpus.root),
        recursive=True,
        device="cpu",
        thresholds=thresholds,
    )
    storage = Storage(app_root / f"{mode}.sqlite3")
    storage.init_schema()
    scan_id = storage.create_scan(
        request.path,
        request.recursive,
        "ViT-B-32",
        "laion2b_s34b_b79k",
        "cpu",
        thresholds.model_dump(),
    )
    run_scan(scan_id, request, storage, "ViT-B-32", "laion2b_s34b_b79k", "cpu")

    scan = storage.get_scan(scan_id)
    assert scan is not None
    assert scan["status"] == "completed", scan["error"]
    groups = storage.get_groups(scan_id, max(1, storage.count_groups(scan_id)), 0)
    return evaluate_scan_groups(
        groups,
        corpus.positive_pairs,
        corpus.negative_pairs,
    )


def make_scene(path: Path, seed: int) -> None:
    rng = random.Random(seed)
    width, height = 192, 160
    background = tuple(rng.randint(205, 245) for _ in range(3))
    image = Image.new("RGB", (width, height), background)
    draw = ImageDraw.Draw(image)
    palette = [
        (rng.randint(20, 80), rng.randint(70, 150), rng.randint(150, 225)),
        (rng.randint(170, 230), rng.randint(35, 95), rng.randint(45, 115)),
        (rng.randint(25, 90), rng.randint(125, 195), rng.randint(55, 135)),
    ]
    for shape_index in range(7):
        x0 = rng.randint(4, 132)
        y0 = rng.randint(4, 104)
        x1 = min(width - 4, x0 + rng.randint(28, 62))
        y1 = min(height - 4, y0 + rng.randint(22, 52))
        color = palette[shape_index % len(palette)]
        if shape_index % 2:
            draw.ellipse((x0, y0, x1, y1), fill=color)
        else:
            draw.rectangle((x0, y0, x1, y1), fill=color)
    for offset in range(0, width, 24):
        draw.line(
            (offset, 0, width - offset // 3, height),
            fill=palette[offset // 24 % len(palette)],
            width=2,
        )
    image.save(path)


def make_positive_variant(
    source: Path,
    target: Path,
    kind: str,
    seed: int,
) -> None:
    image = Image.open(source).convert("RGB")
    if kind == "jpeg":
        image.save(target, quality=76, subsampling=2)
    elif kind == "webp":
        image.save(target, format="WEBP", quality=72, method=6)
    elif kind == "resize":
        image.resize((134, 112), Image.Resampling.BILINEAR).save(target)
    elif kind == "tone":
        toned = ImageEnhance.Brightness(image).enhance(1.04)
        toned = ImageEnhance.Color(toned).enhance(0.96)
        ImageEnhance.Contrast(toned).enhance(1.03).save(target)
    elif kind == "overlay":
        edited = image.copy()
        draw = ImageDraw.Draw(edited)
        rng = random.Random(seed)
        x0 = 166 + rng.randint(0, 4)
        y0 = 136 + rng.randint(0, 4)
        draw.rectangle((x0, y0, x0 + 10, y0 + 8), fill=(24, 24, 24))
        edited.save(target)
    else:
        raise ValueError(f"Unsupported positive transform: {kind}")


def make_hard_negatives(root: Path, seed: int) -> list[Path]:
    paths: list[Path] = []

    vertical_path = root / "negative-palette-vertical.png"
    horizontal_path = root / "negative-palette-horizontal.png"
    vertical = Image.new("RGB", (128, 128), (24, 82, 188))
    ImageDraw.Draw(vertical).rectangle((64, 0, 127, 127), fill=(220, 68, 52))
    horizontal = Image.new("RGB", (128, 128), (24, 82, 188))
    ImageDraw.Draw(horizontal).rectangle((0, 64, 127, 127), fill=(220, 68, 52))
    vertical.save(vertical_path)
    horizontal.save(horizontal_path)
    paths.extend((vertical_path, horizontal_path))

    circle_path = root / "negative-icon-circle.png"
    square_path = root / "negative-icon-square.png"
    circle = Image.new("RGB", (128, 128), (242, 242, 238))
    ImageDraw.Draw(circle).ellipse((34, 34, 94, 94), fill=(42, 126, 190))
    square = Image.new("RGB", (128, 128), (242, 242, 238))
    ImageDraw.Draw(square).rectangle((34, 34, 94, 94), fill=(42, 126, 190))
    circle.save(circle_path)
    square.save(square_path)
    paths.extend((circle_path, square_path))

    e_path = root / "negative-glyph-e.png"
    f_path = root / "negative-glyph-f.png"
    for target, include_bottom in ((e_path, True), (f_path, False)):
        glyph = Image.new("RGB", (128, 128), (248, 248, 248))
        draw = ImageDraw.Draw(glyph)
        draw.rectangle((30, 22, 44, 106), fill=(28, 28, 28))
        draw.rectangle((30, 22, 98, 36), fill=(28, 28, 28))
        draw.rectangle((30, 57, 88, 71), fill=(28, 28, 28))
        if include_bottom:
            draw.rectangle((30, 92, 98, 106), fill=(28, 28, 28))
        glyph.save(target)
    paths.extend((e_path, f_path))

    flat_path = root / "negative-flat.png"
    flat_edit_path = root / "negative-flat-one-pixel.png"
    Image.new("RGB", (96, 96), (210, 20, 20)).save(flat_path)
    flat_edit = Image.new("RGB", (96, 96), (210, 20, 20))
    flat_edit.putpixel((48, 48), (20, 20, 210))
    flat_edit.save(flat_edit_path)
    paths.extend((flat_path, flat_edit_path))

    shifted_a = root / "negative-shift-left.png"
    shifted_b = root / "negative-shift-right.png"
    for target, x_offset in ((shifted_a, 16), (shifted_b, 54)):
        shifted = Image.new("RGB", (160, 128), (236, 238, 232))
        draw = ImageDraw.Draw(shifted)
        draw.rectangle((x_offset, 26, x_offset + 66, 88), fill=(210, 58, 72))
        draw.ellipse((82, 58, 142, 116), fill=(36, 142, 92))
        draw.line((0, 106, 160, 76), fill=(24, 82, 168), width=4)
        shifted.save(target)
    paths.extend((shifted_a, shifted_b))

    similar_a = root / "negative-similar-a.png"
    similar_b = root / "negative-similar-b.png"
    make_scene(similar_a, seed)
    make_scene(similar_b, seed + 1)
    paths.extend((similar_a, similar_b))
    return paths

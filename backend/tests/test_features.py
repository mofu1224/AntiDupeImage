import math
from dataclasses import replace
from pathlib import Path

import pytest

Image = pytest.importorskip("PIL.Image")
ImageDraw = pytest.importorskip("PIL.ImageDraw")

import app.services.features as feature_service
from app.core.config import get_settings
from app.services.features import (
    FEATURE_REVISION,
    ImageFeatureError,
    LIGHTWEIGHT_FEATURE_REVISION,
    dhash_distance,
    edge_similarity,
    extract_image_features,
    extract_lightweight_image_features,
    extract_pixel_digest,
    histogram_similarity,
    layout_similarity,
    phash_distance,
    render_verified_snapshot_rgb,
    structural_similarity,
    whash_distance,
)


def make_image(path: Path, color: tuple[int, int, int], size: tuple[int, int] = (80, 80)) -> None:
    Image.new("RGB", size, color).save(path)


def test_same_pixels_share_digest(tmp_path: Path) -> None:
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    make_image(first, (120, 50, 200))
    make_image(second, (120, 50, 200))

    left = extract_image_features(first)
    right = extract_image_features(second)

    assert left["pixel_digest"] == right["pixel_digest"]
    assert phash_distance(left, right) == 0
    assert dhash_distance(left, right) == 0
    assert whash_distance(left, right) == 0
    assert math.isclose(histogram_similarity(left, right), 1.0)
    assert math.isclose(layout_similarity(left, right), 1.0)
    assert math.isclose(edge_similarity(left, right), 1.0)


def test_same_still_pixels_share_digest_across_lossless_formats(tmp_path: Path) -> None:
    image = Image.new("RGB", (20, 20), (120, 50, 200))
    paths = [
        tmp_path / "same.png",
        tmp_path / "same.gif",
        tmp_path / "same.webp",
        tmp_path / "same.tiff",
    ]
    image.save(paths[0])
    image.save(paths[1])
    image.save(paths[2], lossless=True)
    image.save(paths[3])

    digests = {extract_image_features(path)["pixel_digest"] for path in paths}

    assert len(digests) == 1


def test_exact_digest_includes_alpha_channel(tmp_path: Path) -> None:
    opaque = tmp_path / "opaque.png"
    transparent = tmp_path / "transparent.png"
    Image.new("RGBA", (20, 20), (255, 0, 0, 255)).save(opaque)
    Image.new("RGBA", (20, 20), (255, 0, 0, 0)).save(transparent)

    assert extract_image_features(opaque)["pixel_digest"] != extract_image_features(transparent)[
        "pixel_digest"
    ]


def test_visual_features_include_transparency(tmp_path: Path) -> None:
    opaque = tmp_path / "opaque.png"
    transparent = tmp_path / "transparent.png"
    Image.new("RGBA", (80, 80), (255, 0, 0, 255)).save(opaque)
    Image.new("RGBA", (80, 80), (255, 0, 0, 0)).save(transparent)

    left = extract_image_features(opaque)
    right = extract_image_features(transparent)

    assert histogram_similarity(left, right) < 0.9


def test_exact_digest_includes_all_animation_frames(tmp_path: Path) -> None:
    first = tmp_path / "first.gif"
    second = tmp_path / "second.gif"
    red = Image.new("RGB", (20, 20), (255, 0, 0))
    blue = Image.new("RGB", (20, 20), (0, 0, 255))
    green = Image.new("RGB", (20, 20), (0, 255, 0))
    red.save(first, save_all=True, append_images=[blue], duration=100, loop=0)
    red.save(second, save_all=True, append_images=[green], duration=100, loop=0)

    assert extract_image_features(first)["pixel_digest"] != extract_image_features(second)[
        "pixel_digest"
    ]


def test_exact_digest_includes_animation_playback_metadata(tmp_path: Path) -> None:
    fast = tmp_path / "fast.gif"
    slow = tmp_path / "slow.gif"
    finite = tmp_path / "finite.gif"
    red = Image.new("RGB", (20, 20), (255, 0, 0))
    blue = Image.new("RGB", (20, 20), (0, 0, 255))
    red.save(fast, save_all=True, append_images=[blue], duration=[20, 40], loop=0)
    red.save(slow, save_all=True, append_images=[blue], duration=[200, 400], loop=0)
    red.save(finite, save_all=True, append_images=[blue], duration=[20, 40], loop=3)

    fast_digest = extract_image_features(fast)["pixel_digest"]
    assert fast_digest != extract_image_features(slow)["pixel_digest"]
    assert fast_digest != extract_image_features(finite)["pixel_digest"]

    legacy_revision = "rgba-frames-exif-checkerboard-v4"
    assert extract_pixel_digest(fast, feature_revision=legacy_revision)[1] == extract_pixel_digest(
        slow, feature_revision=legacy_revision
    )[1]


def test_equivalent_animation_metadata_is_canonical_across_formats(tmp_path: Path) -> None:
    red = Image.new("RGB", (20, 20), (255, 0, 0))
    blue = Image.new("RGB", (20, 20), (0, 0, 255))
    gif = tmp_path / "same.gif"
    apng = tmp_path / "same.png"
    webp = tmp_path / "same.webp"
    red.save(
        gif,
        save_all=True,
        append_images=[blue],
        duration=[100, 200],
        loop=2,
        disposal=[1, 1],
    )
    red.save(
        apng,
        save_all=True,
        append_images=[blue],
        duration=[100, 200],
        loop=2,
        disposal=[0, 0],
        blend=[0, 0],
    )
    red.save(
        webp,
        save_all=True,
        append_images=[blue],
        duration=[100, 200],
        loop=2,
        lossless=True,
    )

    digests = {extract_image_features(path)["pixel_digest"] for path in (gif, apng, webp)}

    assert len(digests) == 1


def test_apng_default_image_is_excluded_from_playback_digest(tmp_path: Path) -> None:
    first = tmp_path / "first.png"
    different_poster = tmp_path / "different-poster.png"
    different_animation = tmp_path / "different-animation.png"
    red = Image.new("RGBA", (20, 20), (255, 0, 0, 255))
    yellow = Image.new("RGBA", (20, 20), (255, 255, 0, 255))
    blue = Image.new("RGBA", (20, 20), (0, 0, 255, 255))
    green = Image.new("RGBA", (20, 20), (0, 255, 0, 255))
    purple = Image.new("RGBA", (20, 20), (128, 0, 128, 255))
    options = {
        "save_all": True,
        "default_image": True,
        "duration": [100, 200],
        "loop": 2,
        "disposal": [0, 0],
        "blend": [0, 0],
    }
    red.save(first, append_images=[blue, green], **options)
    yellow.save(different_poster, append_images=[blue, green], **options)
    red.save(different_animation, append_images=[blue, purple], **options)

    first_snapshot = extract_image_features(first)

    assert first_snapshot["pixel_digest"] == extract_image_features(different_poster)[
        "pixel_digest"
    ]
    assert first_snapshot["pixel_digest"] != extract_image_features(different_animation)[
        "pixel_digest"
    ]
    assert render_verified_snapshot_rgb(first_snapshot).getpixel((0, 0)) == (255, 0, 0)
    legacy_revision = "rgba-frames-exif-checkerboard-v4"
    assert extract_pixel_digest(first, feature_revision=legacy_revision)[1] != extract_pixel_digest(
        different_poster, feature_revision=legacy_revision
    )[1]


def test_animation_digest_enforces_frame_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "frames.gif"
    frames = [Image.new("RGB", (10, 10), (index, 0, 0)) for index in range(3)]
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=10, loop=0)
    settings = replace(get_settings(), max_animation_frames=2, max_decoded_pixels=10_000)
    monkeypatch.setattr(feature_service, "get_settings", lambda: settings)

    with pytest.raises(ImageFeatureError, match="2 frame safety limit"):
        extract_image_features(path)


def test_animation_digest_enforces_cumulative_pixel_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "pixels.gif"
    frames = [Image.new("RGB", (10, 10), (index, 0, 0)) for index in range(2)]
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=10, loop=0)
    settings = replace(get_settings(), max_animation_frames=10, max_decoded_pixels=150)
    monkeypatch.setattr(feature_service, "get_settings", lambda: settings)

    with pytest.raises(ImageFeatureError, match="150 cumulative decoded-pixel"):
        extract_image_features(path)


def test_animation_digest_checks_cancellation_between_frames(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "cancel.gif"
    frames = [Image.new("RGB", (10, 10), (index, 0, 0)) for index in range(3)]
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=10, loop=0)
    settings = replace(get_settings(), max_animation_frames=10, max_decoded_pixels=10_000)
    monkeypatch.setattr(feature_service, "get_settings", lambda: settings)
    calls = 0

    def cancel() -> None:
        nonlocal calls
        calls += 1
        if calls >= 2:
            raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        extract_image_features(path, check_cancelled=cancel)

    assert calls >= 2


def test_exif_orientation_is_applied_to_features(tmp_path: Path) -> None:
    path = tmp_path / "oriented.jpg"
    exif = Image.Exif()
    exif[274] = 6
    Image.new("RGB", (20, 40), (10, 20, 30)).save(path, exif=exif)

    feature = extract_image_features(path)

    assert (feature["width"], feature["height"]) == (40, 20)


def test_different_colors_are_less_similar(tmp_path: Path) -> None:
    red = tmp_path / "red.png"
    green = tmp_path / "green.png"
    make_image(red, (220, 20, 20))
    make_image(green, (20, 220, 20))

    left = extract_image_features(red)
    right = extract_image_features(green)

    assert left["pixel_digest"] != right["pixel_digest"]
    assert histogram_similarity(left, right) < 0.75


def test_precision_features_include_layout_edges_and_quality(tmp_path: Path) -> None:
    path = tmp_path / "pattern.png"
    image = Image.new("RGB", (96, 96), (245, 245, 245))
    for offset in range(0, 96, 16):
        for index in range(8):
            image.putpixel((offset + index, 20 + index), (20, 80, 180))
    image.save(path)

    feature = extract_image_features(path)

    assert len(feature["dhash"]) == 16
    assert len(feature["whash"]) == 16
    assert len(feature["luma_layout"]) == 1024
    assert len(feature["edge_layout"]) == 256
    assert feature["quality_score"] > 0


def test_structural_similarity_survives_resize_and_recompression(tmp_path: Path) -> None:
    original = tmp_path / "original.png"
    recompressed = tmp_path / "recompressed.jpg"
    image = Image.new("RGB", (160, 128), (240, 238, 230))
    draw = ImageDraw.Draw(image)
    draw.rectangle((14, 18, 92, 78), fill=(214, 56, 72))
    draw.ellipse((74, 42, 146, 116), fill=(35, 142, 92))
    for offset in range(0, 160, 20):
        draw.line((offset, 0, 160 - offset // 2, 128), fill=(24, 82, 168), width=3)
    image.save(original)
    image.resize((112, 90), Image.Resampling.BILINEAR).save(recompressed, quality=82)

    score = structural_similarity(
        extract_image_features(original),
        extract_image_features(recompressed),
    )

    assert score >= 0.95


def test_structural_similarity_distinguishes_same_palette_layouts(tmp_path: Path) -> None:
    vertical_path = tmp_path / "vertical.png"
    horizontal_path = tmp_path / "horizontal.png"
    vertical = Image.new("RGB", (96, 96), (20, 80, 190))
    ImageDraw.Draw(vertical).rectangle((48, 0, 95, 95), fill=(220, 70, 55))
    horizontal = Image.new("RGB", (96, 96), (20, 80, 190))
    ImageDraw.Draw(horizontal).rectangle((0, 48, 95, 95), fill=(220, 70, 55))
    vertical.save(vertical_path)
    horizontal.save(horizontal_path)

    score = structural_similarity(
        extract_image_features(vertical_path),
        extract_image_features(horizontal_path),
    )

    assert score < 0.95


@pytest.mark.parametrize(
    ("left_layout", "right_layout"),
    [
        ([], []),
        ([0] * 15, [0] * 15),
        ([0] * 256, [0] * 256),
        ([0] * 256, [0] * 1024),
        (["invalid"] * 256, ["invalid"] * 256),
    ],
)
def test_structural_similarity_rejects_missing_or_invalid_layouts(
    left_layout: list[object],
    right_layout: list[object],
) -> None:
    assert (
        structural_similarity(
            {"luma_layout": left_layout},
            {"luma_layout": right_layout},
        )
        == 0.0
    )


def test_lightweight_features_use_separate_revision_and_reduced_layout(tmp_path: Path) -> None:
    path = tmp_path / "pattern.png"
    image = Image.new("RGB", (160, 120), (245, 245, 245))
    image.save(path)

    standard = extract_image_features(path)
    lightweight = extract_lightweight_image_features(path)

    assert standard["feature_revision"] == FEATURE_REVISION
    assert lightweight["feature_revision"] == LIGHTWEIGHT_FEATURE_REVISION
    assert standard["pixel_digest"] == lightweight["pixel_digest"]
    assert (standard["width"], standard["height"]) == (lightweight["width"], lightweight["height"])
    assert len(standard["luma_layout"]) == 1024
    assert len(lightweight["luma_layout"]) == 256
    assert len(lightweight["edge_layout"]) == 64

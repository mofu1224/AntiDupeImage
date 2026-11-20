from __future__ import annotations

import hashlib
import math
import os
import stat
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, BinaryIO, cast

import numpy as np
from PIL import Image, ImageDraw, ImageOps, ImageSequence

from app.core.config import get_settings


IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".bmp",
    ".gif",
    ".tif",
    ".tiff",
    ".avif",
}
FEATURE_REVISION = "rgba-frames-playback-exif-checkerboard-v5"
LIGHTWEIGHT_FEATURE_REVISION = "rgba-frames-playback-exif-checkerboard-lightweight-v2"
_PLAYBACK_FEATURE_REVISIONS = frozenset({FEATURE_REVISION, LIGHTWEIGHT_FEATURE_REVISION})
_LEGACY_FRAME_FEATURE_REVISIONS = frozenset(
    {
        "",
        "rgba-frames-exif-checkerboard-v3",
        "rgba-frames-exif-checkerboard-v4",
        "rgba-frames-exif-checkerboard-lightweight-v1",
    }
)
_PLAYBACK_IMAGE_FORMATS = frozenset({"AVIF", "GIF", "PNG", "WEBP"})
_PLAYBACK_METADATA_DEFAULTS = {
    "loop": 1,
    "duration": 0,
}


class ImageFeatureError(RuntimeError):
    pass


class UnsafeImagePathError(ImageFeatureError):
    pass


class ImageChangedError(ImageFeatureError):
    pass


@dataclass(frozen=True)
class FileIdentity:
    path: str
    file_dev: int
    file_ino: int
    mtime_ns: int
    file_size: int


@dataclass
class OpenedImage:
    identity: FileIdentity
    image: Image.Image
    file: BinaryIO
    initial_stat: os.stat_result


def is_supported_image(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_EXTENSIONS


def get_file_identity(path: Path) -> FileIdentity:
    expanded = path.expanduser()
    if expanded.is_symlink():
        raise UnsafeImagePathError(f"Symbolic links are not allowed: {expanded}")
    try:
        resolved = expanded.resolve(strict=True)
        file_stat = resolved.stat()
    except FileNotFoundError as exc:
        raise ImageFeatureError(f"Image file no longer exists: {expanded}") from exc
    except OSError as exc:
        raise ImageFeatureError(f"Unable to inspect image file: {expanded}: {exc}") from exc
    if resolved != expanded.absolute():
        raise UnsafeImagePathError(f"Symbolic link path components are not allowed: {expanded}")
    if not stat.S_ISREG(file_stat.st_mode):
        raise UnsafeImagePathError(f"Image path is not a regular file: {resolved}")
    return FileIdentity(
        path=str(resolved),
        file_dev=int(getattr(file_stat, "st_dev", 0) or 0),
        file_ino=int(getattr(file_stat, "st_ino", 0) or 0),
        mtime_ns=int(file_stat.st_mtime_ns),
        file_size=int(file_stat.st_size),
    )


def extract_image_features(path: Path, check_cancelled: Any | None = None) -> dict[str, Any]:
    return _extract_image_features(path, lightweight=False, check_cancelled=check_cancelled)


def extract_lightweight_image_features(
    path: Path, check_cancelled: Any | None = None
) -> dict[str, Any]:
    return _extract_image_features(path, lightweight=True, check_cancelled=check_cancelled)


def _extract_image_features(
    path: Path,
    *,
    lightweight: bool,
    check_cancelled: Any | None,
) -> dict[str, Any]:
    try:
        with open_verified_image(path) as opened:
            identity_before = opened.identity
            image = opened.image
            pixel_digest = _decoded_pixel_digest(image, check_cancelled=check_cancelled)
            image.seek(0)
            display = render_display_rgb(image)
            width, height = display.size
            visual = _lightweight_visual(display) if lightweight else display

            phash_parts = _channel_phash(visual)
            phash = ":".join(f"{part:016x}" for part in phash_parts)
            phash_int = "|".join(str(part) for part in phash_parts)
            dhash = f"{_dhash(visual):016x}"
            whash = f"{_whash(visual):016x}"

            sample_size = 64 if lightweight else 128
            sample = visual.resize((sample_size, sample_size), Image.Resampling.BILINEAR)
            arr = np.asarray(sample, dtype=np.float32)
            avg_rgb = arr.reshape(-1, 3).mean(axis=0).round(4).tolist()
            color_hist = _rgb_histogram(arr)
            hsv_hist = _hsv_histogram(sample)
            luma_layout = _luma_layout(visual, size=16 if lightweight else 32)
            edge_layout = _edge_layout(visual, size=16 if lightweight else 32)
            quality_score = _quality_score(sample)
    except ImageFeatureError:
        raise
    except Exception as exc:
        if check_cancelled is not None:
            check_cancelled()
        raise ImageFeatureError(f"Unable to decode image: {path}: {exc}") from exc

    return {
        **asdict(identity_before),
        "feature_revision": LIGHTWEIGHT_FEATURE_REVISION if lightweight else FEATURE_REVISION,
        "width": int(width),
        "height": int(height),
        "pixel_digest": pixel_digest,
        "phash": phash,
        "phash_parts": phash_parts,
        "phash_int": phash_int,
        "dhash": dhash,
        "whash": whash,
        "avg_rgb": avg_rgb,
        "color_hist": color_hist,
        "hsv_hist": hsv_hist,
        "luma_layout": luma_layout,
        "edge_layout": edge_layout,
        "quality_score": quality_score,
    }


def extract_pixel_digest(
    path: Path,
    check_cancelled: Any | None = None,
    *,
    feature_revision: str | None = None,
) -> tuple[FileIdentity, str]:
    try:
        with open_verified_image(path) as opened:
            digest = _decoded_pixel_digest(
                opened.image,
                check_cancelled=check_cancelled,
                include_playback_metadata=_revision_includes_playback(feature_revision),
            )
            identity = opened.identity
    except Exception as exc:
        if check_cancelled is not None:
            check_cancelled()
        raise ImageFeatureError(f"Unable to decode image: {path}: {exc}") from exc
    return identity, digest


def render_verified_snapshot_rgb(
    snapshot: dict[str, Any],
    *,
    check_cancelled: Any | None = None,
) -> Image.Image:
    path = Path(snapshot["path"])
    try:
        with open_verified_image(path) as opened:
            if not file_identity_matches_snapshot(snapshot, opened.identity):
                raise ImageChangedError("File identity changed after the scan")
            digest = _decoded_pixel_digest(
                opened.image,
                check_cancelled=check_cancelled,
                include_playback_metadata=_revision_includes_playback(
                    cast(str | None, snapshot.get("feature_revision"))
                ),
            )
            if digest != snapshot["pixel_digest"]:
                raise ImageChangedError("Decoded image content changed after the scan")
            opened.image.seek(0)
            return render_display_rgb(opened.image)
    except ImageFeatureError:
        raise
    except Exception as exc:
        if check_cancelled is not None:
            check_cancelled()
        raise ImageFeatureError(f"Unable to decode image: {path}: {exc}") from exc


def file_identity_matches_snapshot(snapshot: dict[str, Any], identity: FileIdentity) -> bool:
    expected_dev = int(snapshot.get("file_dev", 0) or 0)
    expected_ino = int(snapshot.get("file_ino", 0) or 0)
    if expected_dev and int(identity.file_dev) != expected_dev:
        return False
    if expected_ino and int(identity.file_ino) != expected_ino:
        return False
    return (
        int(identity.mtime_ns) == int(snapshot["mtime_ns"])
        and int(identity.file_size) == int(snapshot["file_size"])
    )


@contextmanager
def open_verified_image(path: Path):
    identity = get_file_identity(path)
    descriptor = -1
    file: BinaryIO | None = None
    image: Image.Image | None = None
    try:
        descriptor = os.open(
            identity.path,
            os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        initial_stat = os.fstat(descriptor)
        if _identity_from_stat(Path(identity.path), initial_stat) != identity:
            raise ImageChangedError(f"Image changed while it was being opened: {identity.path}")
        file = os.fdopen(descriptor, "rb")
        descriptor = -1
        image = Image.open(file)
        yield OpenedImage(identity=identity, image=image, file=file, initial_stat=initial_stat)
        final_stat = os.fstat(file.fileno())
        if _identity_from_stat(Path(identity.path), final_stat) != identity:
            raise ImageChangedError(f"Image changed while it was open: {identity.path}")
        current_identity = get_file_identity(Path(identity.path))
        if current_identity != identity:
            raise ImageChangedError(f"Image path changed while it was open: {identity.path}")
    except ImageFeatureError:
        raise
    except OSError as exc:
        raise ImageFeatureError(f"Unable to open image file safely: {identity.path}: {exc}") from exc
    finally:
        if image is not None:
            image.close()
        if file is not None:
            file.close()
        if descriptor >= 0:
            os.close(descriptor)


def phash_distance(left: dict[str, Any], right: dict[str, Any]) -> int:
    left_parts = left.get("phash_parts") or _parse_phash(left["phash"])
    right_parts = right.get("phash_parts") or _parse_phash(right["phash"])
    distances = [(a ^ b).bit_count() for a, b in zip(left_parts, right_parts)]
    return int(round(sum(distances) / max(1, len(distances))))


def phash_total_distance(left: dict[str, Any], right: dict[str, Any]) -> int:
    left_parts = left.get("phash_parts") or _parse_phash(left["phash"])
    right_parts = right.get("phash_parts") or _parse_phash(right["phash"])
    return sum((a ^ b).bit_count() for a, b in zip(left_parts, right_parts))


def dhash_distance(left: dict[str, Any], right: dict[str, Any]) -> int:
    return _hex_hash_distance(str(left.get("dhash", "")), str(right.get("dhash", "")))


def whash_distance(left: dict[str, Any], right: dict[str, Any]) -> int:
    return _hex_hash_distance(str(left.get("whash", "")), str(right.get("whash", "")))


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return max(0.0, min(1.0, dot / (left_norm * right_norm)))


def histogram_similarity(left: dict[str, Any], right: dict[str, Any]) -> float:
    rgb_score = cosine_similarity(left["color_hist"], right["color_hist"])
    hsv_score = cosine_similarity(left["hsv_hist"], right["hsv_hist"])
    return (rgb_score * 0.65) + (hsv_score * 0.35)


def mean_color_similarity(left: dict[str, Any], right: dict[str, Any]) -> float:
    left_rgb = left.get("avg_rgb")
    right_rgb = right.get("avg_rgb")
    if (
        not isinstance(left_rgb, list)
        or not isinstance(right_rgb, list)
        or len(left_rgb) != 3
        or len(right_rgb) != 3
    ):
        return 0.0
    try:
        squared_error = sum(
            (float(left_value) - float(right_value)) ** 2
            for left_value, right_value in zip(left_rgb, right_rgb)
        )
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if not math.isfinite(squared_error):
        return 0.0
    normalized_rmse = math.sqrt(squared_error / 3.0) / 255.0
    return max(0.0, min(1.0, 1.0 - normalized_rmse))


def layout_similarity(left: dict[str, Any], right: dict[str, Any]) -> float:
    return _vector_similarity(left.get("luma_layout", []), right.get("luma_layout", []))


def structural_similarity(left: dict[str, Any], right: dict[str, Any]) -> float:
    """Compare cached luma layouts with a deterministic two-scale SSIM score."""
    left_signature = _structural_signature(left)
    right_signature = _structural_signature(right)
    if left_signature is None or right_signature is None:
        return 0.0
    if left.get("luma_layout") is right.get("luma_layout"):
        return 1.0

    return _structural_signature_similarity(left_signature, right_signature)


def edge_similarity(left: dict[str, Any], right: dict[str, Any]) -> float:
    return _vector_similarity(left.get("edge_layout", []), right.get("edge_layout", []))


def image_score(width: int, height: int, size_bytes: int) -> int:
    return (width * height * 10_000_000) + size_bytes


def feature_sort_score(feature: dict[str, Any]) -> int:
    return image_score(int(feature["width"]), int(feature["height"]), int(feature["file_size"]))


def hydrate_cached_feature(row: dict[str, Any]) -> dict[str, Any]:
    feature = dict(row)
    feature["phash_parts"] = _parse_phash_int(feature.get("phash_int")) or _parse_phash(
        feature["phash"]
    )
    return feature


def render_display_rgb(image: Image.Image) -> Image.Image:
    normalized = ImageOps.exif_transpose(image.copy()).convert("RGBA")
    width, height = normalized.size
    tile = max(8, max(width, height) // 64)
    background = Image.new("RGBA", normalized.size, (238, 238, 238, 255))
    draw = ImageDraw.Draw(background)
    for y in range(0, height, tile):
        for x in range(0, width, tile):
            if ((x // tile) + (y // tile)) % 2:
                draw.rectangle(
                    (x, y, min(width - 1, x + tile - 1), min(height - 1, y + tile - 1)),
                    fill=(204, 204, 204, 255),
                )
    return Image.alpha_composite(background, normalized).convert("RGB")


def _lightweight_visual(rgb: Image.Image) -> Image.Image:
    visual = rgb.copy()
    visual.thumbnail((512, 512), Image.Resampling.BILINEAR)
    return visual


def _decoded_pixel_digest(
    image: Image.Image,
    *,
    check_cancelled: Any | None = None,
    max_frames: int | None = None,
    max_decoded_pixels: int | None = None,
    include_playback_metadata: bool = True,
) -> str:
    settings = get_settings()
    frame_limit = settings.max_animation_frames if max_frames is None else max_frames
    pixel_limit = settings.max_decoded_pixels if max_decoded_pixels is None else max_decoded_pixels
    digest = hashlib.sha256()
    if include_playback_metadata:
        digest.update(b"AntiDupeImage decoded RGBA frames and playback v3\0")
    else:
        digest.update(b"AntiDupeImage decoded RGBA frames v2\0")
    is_playback_animation = include_playback_metadata and _is_playback_animation(image)
    if is_playback_animation:
        _update_digest_metadata(digest, "loop", image.info.get("loop"))
    frame_count = 0
    decoded_pixels = 0
    frames = ImageSequence.Iterator(image)
    if is_playback_animation and _has_separate_default_image(image):
        next(frames, None)
    for frame in frames:
        if check_cancelled is not None:
            check_cancelled()
        if frame_count >= frame_limit:
            raise ImageFeatureError(f"Image exceeds the {frame_limit} frame safety limit")
        frame_pixels = int(frame.width) * int(frame.height)
        if decoded_pixels + frame_pixels > pixel_limit:
            raise ImageFeatureError(
                f"Image exceeds the {pixel_limit} cumulative decoded-pixel safety limit"
            )
        normalized = ImageOps.exif_transpose(frame.copy()).convert("RGBA")
        width, height = normalized.size
        digest.update(width.to_bytes(8, "big", signed=False))
        digest.update(height.to_bytes(8, "big", signed=False))
        if is_playback_animation:
            _update_digest_metadata(digest, "duration", frame.info.get("duration"))
        digest.update(normalized.tobytes())
        frame_count += 1
        decoded_pixels += frame_pixels
    digest.update(frame_count.to_bytes(8, "big", signed=False))
    return digest.hexdigest()


def _update_digest_metadata(digest: Any, name: str, value: Any) -> None:
    encoded_name = name.encode("ascii")
    if name not in _PLAYBACK_METADATA_DEFAULTS:
        raise ImageFeatureError(f"Unsupported playback metadata field: {name}")
    canonical_value = _PLAYBACK_METADATA_DEFAULTS[name] if value is None else value
    try:
        numeric_value = Decimal(str(canonical_value))
    except (InvalidOperation, ValueError) as exc:
        raise ImageFeatureError(f"Invalid {name} playback metadata: {value!r}") from exc
    if not numeric_value.is_finite():
        raise ImageFeatureError(f"Invalid {name} playback metadata: {value!r}")
    encoded_value = format(numeric_value.normalize(), "f").encode("ascii")
    digest.update(len(encoded_name).to_bytes(2, "big", signed=False))
    digest.update(encoded_name)
    digest.update(len(encoded_value).to_bytes(8, "big", signed=False))
    digest.update(encoded_value)


def _revision_includes_playback(feature_revision: str | None) -> bool:
    if feature_revision is None or feature_revision in _PLAYBACK_FEATURE_REVISIONS:
        return True
    if feature_revision in _LEGACY_FRAME_FEATURE_REVISIONS:
        return False
    raise ImageFeatureError(f"Unsupported feature revision for digest verification: {feature_revision}")


def _is_playback_animation(image: Image.Image) -> bool:
    return image.format in _PLAYBACK_IMAGE_FORMATS and bool(
        getattr(image, "is_animated", False)
    )


def _has_separate_default_image(image: Image.Image) -> bool:
    return image.format == "PNG" and image.info.get("default_image") is True


def _identity_from_stat(path: Path, metadata: os.stat_result) -> FileIdentity:
    if not stat.S_ISREG(metadata.st_mode):
        raise UnsafeImagePathError(f"Image path is not a regular file: {path}")
    return FileIdentity(
        path=str(path),
        file_dev=int(getattr(metadata, "st_dev", 0) or 0),
        file_ino=int(getattr(metadata, "st_ino", 0) or 0),
        mtime_ns=int(metadata.st_mtime_ns),
        file_size=int(metadata.st_size),
    )


def _channel_phash(rgb: Image.Image) -> list[int]:
    try:
        import imagehash
    except ImportError:
        return [_average_hash(channel) for channel in rgb.split()]
    return [int(str(imagehash.phash(channel, hash_size=8)), 16) for channel in rgb.split()]


def _dhash(rgb: Image.Image) -> int:
    gray = rgb.convert("L")
    try:
        import imagehash
    except ImportError:
        return _difference_hash(gray)
    return int(str(imagehash.dhash(gray, hash_size=8)), 16)


def _whash(rgb: Image.Image) -> int:
    gray = rgb.convert("L")
    try:
        import imagehash
    except ImportError:
        return _average_hash(gray)
    whash_value = cast(Any, imagehash).whash(gray, hash_size=8)
    return int(str(whash_value), 16)


def _average_hash(channel: Image.Image) -> int:
    small = channel.resize((8, 8), Image.Resampling.BILINEAR)
    arr = np.asarray(small, dtype=np.float32)
    threshold = float(arr.mean())
    bits = 0
    for value in arr.flatten():
        bits = (bits << 1) | int(value >= threshold)
    return bits


def _difference_hash(channel: Image.Image) -> int:
    small = channel.resize((9, 8), Image.Resampling.BILINEAR)
    arr = np.asarray(small, dtype=np.float32)
    bits = 0
    for value in (arr[:, 1:] >= arr[:, :-1]).flatten():
        bits = (bits << 1) | int(value)
    return bits


def _luma_layout(rgb: Image.Image, *, size: int = 32) -> list[int]:
    small = rgb.convert("L").resize((size, size), Image.Resampling.BILINEAR)
    arr = np.asarray(small, dtype=np.uint8)
    return [int(value) for value in arr.flatten()]


def _edge_layout(rgb: Image.Image, *, size: int = 32) -> list[int]:
    small = rgb.convert("L").resize((size, size), Image.Resampling.BILINEAR)
    arr = np.asarray(small, dtype=np.float32)
    dx = np.zeros_like(arr)
    dy = np.zeros_like(arr)
    dx[:, 1:] = np.abs(arr[:, 1:] - arr[:, :-1])
    dy[1:, :] = np.abs(arr[1:, :] - arr[:-1, :])
    edge = np.clip(dx + dy, 0, 255)
    pooled_size = max(1, size // 2)
    pooled = edge.reshape(pooled_size, 2, pooled_size, 2).mean(axis=(1, 3)).round()
    return [int(value) for value in pooled.astype(np.uint8).flatten()]


def _quality_score(image: Image.Image) -> float:
    gray = np.asarray(image.convert("L"), dtype=np.float32)
    contrast = min(1.0, float(gray.std()) / 64.0)
    hist, _ = np.histogram(gray, bins=32, range=(0, 256))
    total = float(hist.sum())
    if total > 0:
        probs = hist.astype(np.float64) / total
        probs = probs[probs > 0]
        entropy = float(-(probs * np.log2(probs)).sum() / math.log2(32))
    else:
        entropy = 0.0
    dx = np.abs(np.diff(gray, axis=1))
    dy = np.abs(np.diff(gray, axis=0))
    edge_strength = min(1.0, (float(dx.mean()) + float(dy.mean())) / 96.0)
    score = (contrast * 0.45) + (entropy * 0.35) + (edge_strength * 0.20)
    return round(max(0.0, min(1.0, score)), 4)


def _rgb_histogram(arr: np.ndarray) -> list[float]:
    values: list[float] = []
    for channel in range(3):
        hist, _ = np.histogram(arr[:, :, channel], bins=16, range=(0, 256))
        values.extend(hist.astype(np.float32).tolist())
    return _normalize(values)


def _hsv_histogram(image: Image.Image) -> list[float]:
    hsv = np.asarray(image.convert("HSV"), dtype=np.float32)
    values: list[float] = []
    for channel, bins in enumerate((16, 8, 8)):
        hist, _ = np.histogram(hsv[:, :, channel], bins=bins, range=(0, 256))
        values.extend(hist.astype(np.float32).tolist())
    return _normalize(values)


def _normalize(values: list[float]) -> list[float]:
    total = float(sum(values))
    if total <= 0:
        return values
    return [round(value / total, 8) for value in values]


def _vector_similarity(left: Any, right: Any) -> float:
    if not isinstance(left, list) or not isinstance(right, list):
        return 0.0
    left_values = cast(list[float], left)
    right_values = cast(list[float], right)
    if len(left_values) != len(right_values) or not left_values:
        return 0.0
    left_arr = np.asarray(left_values, dtype=np.float32)
    right_arr = np.asarray(right_values, dtype=np.float32)
    rmse = float(np.sqrt(np.mean((left_arr - right_arr) ** 2)))
    return max(0.0, min(1.0, 1.0 - (rmse / 255.0)))


StructuralSignature = tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]
_STRUCTURAL_SIGNATURE_KEY = "_structural_similarity_signature"
_STRUCTURAL_LAYOUT_SIDE = 32
_SSIM_C1 = (0.01 * 255.0) ** 2
_SSIM_C2 = (0.03 * 255.0) ** 2


def _structural_signature(feature: dict[str, Any]) -> StructuralSignature | None:
    if _STRUCTURAL_SIGNATURE_KEY in feature:
        return cast(StructuralSignature | None, feature[_STRUCTURAL_SIGNATURE_KEY])

    matrix = _layout_matrix(feature.get("luma_layout"))
    if matrix is None:
        feature[_STRUCTURAL_SIGNATURE_KEY] = None
        return None

    pooled = matrix.reshape(matrix.shape[0] // 2, 2, matrix.shape[1] // 2, 2).mean(axis=(1, 3))
    base_windows = _structural_windows(matrix)
    pooled_windows = _structural_windows(pooled)
    windows = np.concatenate((base_windows, pooled_windows), axis=0)
    means = windows.mean(axis=1)
    centered = windows - means[:, None]
    variances = np.mean(centered * centered, axis=1)
    weights = np.concatenate(
        (
            np.full(base_windows.shape[0], 0.70 / base_windows.shape[0], dtype=np.float32),
            np.full(pooled_windows.shape[0], 0.30 / pooled_windows.shape[0], dtype=np.float32),
        )
    )
    signature = (means, variances, centered, weights)
    feature[_STRUCTURAL_SIGNATURE_KEY] = signature
    return signature


def _layout_matrix(value: Any) -> np.ndarray | None:
    if not isinstance(value, list) or not value:
        return None
    try:
        values = np.asarray(value, dtype=np.float32)
    except (TypeError, ValueError, OverflowError):
        return None
    if values.size != _STRUCTURAL_LAYOUT_SIDE**2:
        return None
    if not bool(np.isfinite(values).all()):
        return None
    return values.reshape(_STRUCTURAL_LAYOUT_SIDE, _STRUCTURAL_LAYOUT_SIDE)


def _structural_windows(matrix: np.ndarray) -> np.ndarray:
    side = int(matrix.shape[0])
    return matrix.reshape(side // 8, 8, side // 8, 8).transpose(0, 2, 1, 3).reshape(-1, 64)


def _structural_signature_similarity(
    left: StructuralSignature,
    right: StructuralSignature,
) -> float:
    left_means, left_variances, left_centered, left_weights = left
    right_means, right_variances, right_centered, right_weights = right
    if left_centered.shape != right_centered.shape or left_weights.shape != right_weights.shape:
        return 0.0

    covariance = np.einsum("ij,ij->i", left_centered, right_centered) / 64.0
    numerator = (2.0 * left_means * right_means + _SSIM_C1) * (2.0 * covariance + _SSIM_C2)
    denominator = (left_means * left_means + right_means * right_means + _SSIM_C1) * (
        left_variances + right_variances + _SSIM_C2
    )
    scores = np.clip(numerator / denominator, -1.0, 1.0)
    return max(0.0, min(1.0, float(np.dot(scores, left_weights))))


def _hex_hash_distance(left: str, right: str) -> int:
    if not left or not right:
        return 64
    try:
        return (int(left, 16) ^ int(right, 16)).bit_count()
    except ValueError:
        return 64


def _parse_phash(value: str) -> list[int]:
    return [int(part, 16) for part in value.split(":") if part]


def _parse_phash_int(value: str | None) -> list[int]:
    if not value:
        return []
    return [int(part) for part in value.split("|") if part]

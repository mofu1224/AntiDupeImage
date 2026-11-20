from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol, cast

from PIL import Image

from app.services.features import render_display_rgb


ProgressCallback = Callable[[int, int], None]
CancelCallback = Callable[[], None]


class ClipModel(Protocol):
    def eval(self) -> object: ...

    def encode_image(self, batch: Any) -> Any: ...


class ClipPreprocess(Protocol):
    def __call__(self, image: Image.Image) -> Any: ...


@dataclass(frozen=True)
class ClipRuntimeInfo:
    available: bool
    version: str | None
    error: str | None


@dataclass
class EmbedResult:
    vectors: dict[str, list[float]]
    failures: dict[str, str]


_MODEL_CACHE: OrderedDict[tuple[str, str, str], tuple[ClipModel, ClipPreprocess]] = OrderedDict()
_MODEL_CACHE_LOCK = threading.RLock()
_MAX_CACHED_MODELS = 2


def get_clip_runtime_info() -> ClipRuntimeInfo:
    try:
        import open_clip  # type: ignore[reportMissingTypeStubs]
        import torch  # type: ignore[reportMissingTypeStubs]
    except Exception as exc:
        return ClipRuntimeInfo(available=False, version=None, error=str(exc))
    open_clip_module = cast(Any, open_clip)
    torch_module = cast(Any, torch)
    version = f"open_clip={getattr(open_clip_module, '__version__', 'unknown')};torch={getattr(torch_module, '__version__', 'unknown')}"
    return ClipRuntimeInfo(available=True, version=version, error=None)


def clip_revision(model_name: str, pretrained: str) -> str:
    info = get_clip_runtime_info()
    return f"{info.version or 'unavailable'};model={model_name};pretrained={pretrained}"


class ClipEmbedder:
    def __init__(self, model_name: str, pretrained: str, device: str):
        self.model_name = model_name
        self.pretrained = pretrained
        self.device = device
        self.revision = clip_revision(model_name, pretrained)
        self._model: ClipModel | None = None
        self._preprocess: ClipPreprocess | None = None

    def embed_images(
        self,
        paths: list[str],
        batch_size: int = 16,
        on_progress: ProgressCallback | None = None,
        check_cancelled: CancelCallback | None = None,
    ) -> EmbedResult:
        if not paths:
            return EmbedResult(vectors={}, failures={})
        if check_cancelled:
            check_cancelled()
        self._ensure_loaded()
        if check_cancelled:
            check_cancelled()

        import torch  # type: ignore[reportMissingTypeStubs]

        torch_module = cast(Any, torch)
        model = self._model
        preprocess = self._preprocess
        if model is None or preprocess is None:
            raise RuntimeError("OpenCLIP model was not loaded")

        results: dict[str, list[float]] = {}
        failures: dict[str, str] = {}
        current_batch_size = max(1, batch_size)
        index = 0
        while index < len(paths):
            if check_cancelled:
                check_cancelled()
            batch_paths = paths[index : index + current_batch_size]
            tensors: list[Any] = []
            valid_paths: list[str] = []
            for item in batch_paths:
                if check_cancelled:
                    check_cancelled()
                try:
                    with Image.open(Path(item)) as image:
                        display = render_display_rgb(image)
                        tensors.append(preprocess(display))
                        valid_paths.append(item)
                except Exception as exc:
                    failures[item] = str(exc)

            if not tensors:
                index += len(batch_paths)
                if on_progress:
                    on_progress(index, len(paths))
                continue

            batch = None
            features = None
            try:
                batch = torch_module.stack(tensors).to(self.device)
                with torch_module.inference_mode():
                    features = model.encode_image(batch)
                    features = features / features.norm(dim=-1, keepdim=True).clamp_min(1e-12)
            except RuntimeError as exc:
                if self._is_out_of_memory(exc):
                    batch = None
                    features = None
                    self._empty_device_cache()
                    if current_batch_size > 1:
                        current_batch_size = max(1, current_batch_size // 2)
                        continue
                raise RuntimeError(f"OpenCLIP inference failed on {self.device}: {exc}") from exc

            vectors = cast(list[list[float]], features.detach().cpu().tolist())
            batch = None
            features = None
            for path, vector in zip(valid_paths, vectors):
                results[path] = [float(value) for value in vector]
            index += len(batch_paths)
            if on_progress:
                on_progress(index, len(paths))

        return EmbedResult(vectors=results, failures=failures)

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        try:
            import open_clip  # type: ignore[reportMissingTypeStubs]
            import torch  # type: ignore[reportMissingTypeStubs]
        except Exception as exc:
            raise RuntimeError(
                "OpenCLIP/PyTorch is not installed. Install the backend 'ml' extra or disable CLIP."
            ) from exc

        open_clip_module = cast(Any, open_clip)
        torch_module = cast(Any, torch)
        key = (self.model_name, self.pretrained, self.device)
        with _MODEL_CACHE_LOCK:
            cached = _MODEL_CACHE.get(key)
            if cached:
                _MODEL_CACHE.move_to_end(key)
                self._model, self._preprocess = cached
                return

            try:
                model_obj, _, preprocess_obj = open_clip_module.create_model_and_transforms(
                    self.model_name,
                    pretrained=self.pretrained,
                    device=self.device,
                )
            except Exception as exc:
                raise RuntimeError(
                    f"Unable to load OpenCLIP model '{self.model_name}' with pretrained '{self.pretrained}': {exc}"
                ) from exc
            model = cast(ClipModel, model_obj)
            preprocess = cast(ClipPreprocess, preprocess_obj)
            model.eval()
            if self.device.startswith("cuda"):
                torch_module.backends.cudnn.benchmark = True
            self._model = model
            self._preprocess = preprocess
            _MODEL_CACHE[key] = (model, preprocess)
            _MODEL_CACHE.move_to_end(key)
            while len(_MODEL_CACHE) > _MAX_CACHED_MODELS:
                _MODEL_CACHE.popitem(last=False)

    def _empty_device_cache(self) -> None:
        try:
            import torch  # type: ignore[reportMissingTypeStubs]

            torch_module = cast(Any, torch)
            if self.device.startswith("cuda"):
                torch_module.cuda.empty_cache()
            elif self.device == "mps" and hasattr(torch_module, "mps"):
                torch_module.mps.empty_cache()
        except Exception:
            pass

    @staticmethod
    def _is_out_of_memory(exc: RuntimeError) -> bool:
        return "out of memory" in str(exc).lower()

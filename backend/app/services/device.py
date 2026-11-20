from __future__ import annotations

import platform
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class DeviceSelection:
    selected: str
    available: list[str]
    cuda_devices: list[str]
    mps_available: bool
    torch_available: bool
    torch_version: str | None


def get_device_info(preferred: str = "auto", gpu_index: int | None = None) -> DeviceSelection:
    try:
        import torch  # type: ignore[import-not-found,import-untyped]
    except Exception:
        return DeviceSelection(
            selected="cpu",
            available=["cpu"],
            cuda_devices=[],
            mps_available=False,
            torch_available=False,
            torch_version=None,
        )

    available: list[str] = ["cpu"]
    cuda_devices: list[str] = []
    if torch.cuda.is_available():  # type: ignore[union-attr]
        for index in range(torch.cuda.device_count()):  # type: ignore[union-attr]
            cuda_devices.append(torch.cuda.get_device_name(index))  # type: ignore[union-attr]
        available.extend(f"cuda:{index}" for index in range(len(cuda_devices)))

    mps_available = bool(
        getattr(torch, "backends", None)  # type: ignore[arg-type]
        and getattr(torch.backends, "mps", None)  # type: ignore[union-attr]
        and torch.backends.mps.is_available()  # type: ignore[union-attr]
        and torch.backends.mps.is_built()  # type: ignore[union-attr]
    )
    if mps_available:
        available.append("mps")

    selected = _select_device(preferred, gpu_index, available, bool(cuda_devices), mps_available)
    return DeviceSelection(
        selected=selected,
        available=available,
        cuda_devices=cuda_devices,
        mps_available=mps_available,
        torch_available=True,
        torch_version=getattr(torch, "__version__", None),  # type: ignore[arg-type]
    )


def select_device(preferred: str = "auto", gpu_index: int | None = None) -> str:
    info = get_device_info(preferred, gpu_index)
    normalized = (preferred or "auto").lower()
    if normalized == "auto":
        return info.selected
    candidate = _normalized_candidate(normalized, gpu_index)
    if candidate not in info.available:
        raise ValueError(
            f"Requested device '{candidate}' is not available. Available devices: {', '.join(info.available)}"
        )
    return candidate


def _normalized_candidate(preferred: str, gpu_index: int | None) -> str:
    if preferred == "cuda":
        return f"cuda:{0 if gpu_index is None else gpu_index}"
    return preferred


def _select_device(
    preferred: str,
    gpu_index: int | None,
    available: list[str],
    has_cuda: bool,
    has_mps: bool,
) -> str:
    normalized = (preferred or "auto").lower()
    if normalized == "auto":
        if has_cuda:
            index = 0 if gpu_index is None else gpu_index
            candidate = f"cuda:{index}"
            return candidate if candidate in available else "cuda:0"
        if has_mps:
            return "mps"
        return "cpu"

    candidate = _normalized_candidate(normalized, gpu_index)
    if candidate in available:
        return candidate
    return "cpu"


def runtime_summary() -> dict[str, str]:
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
    }

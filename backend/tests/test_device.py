import pytest

from app.services.device import DeviceSelection, get_device_info, select_device


def test_device_info_has_cpu_fallback() -> None:
    info = get_device_info("not-a-device")
    assert "cpu" in info.available
    assert info.selected in info.available


def test_explicit_unavailable_device_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    def cpu_only_device(preferred: str, gpu_index: int | None = None) -> DeviceSelection:
        return DeviceSelection(
            selected="cpu",
            available=["cpu"],
            cuda_devices=[],
            mps_available=False,
            torch_available=False,
            torch_version=None,
        )

    monkeypatch.setattr(
        "app.services.device.get_device_info",
        cpu_only_device,
    )

    with pytest.raises(ValueError):
        select_device("cuda", 0)

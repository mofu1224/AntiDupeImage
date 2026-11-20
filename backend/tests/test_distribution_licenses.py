from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.collect_distribution_licenses import collect


def test_backend_package_preserves_project_license():
    root = Path(__file__).resolve().parents[2]
    assert (root / "backend/LICENSE").read_bytes() == (root / "LICENSE").read_bytes()


def test_frontend_original_notices_are_preserved_and_missing_notices_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("importlib.metadata.distributions", lambda: [])
    frontend = tmp_path / "frontend"
    package = frontend / "node_modules" / "example"
    package.mkdir(parents=True)
    original = b"Copyright Example\r\nPermission notice\r\n"
    (package / "LICENSE").write_bytes(original)
    (frontend / "package-lock.json").write_text(json.dumps({"packages": {
        "": {}, "node_modules/example": {"version": "1.0.0", "license": "MIT"},
        "node_modules/missing": {"version": "2.0.0"},
        "node_modules/test-only": {"version": "1.0.0", "dev": True},
    }}))
    output = tmp_path / "notices"
    report = collect(frontend, output)
    assert (output / "npm/node_modules/example/LICENSE").read_bytes() == original
    assert len(report["packages"]) == 2
    assert report["missing_notices"] == ["npm:node_modules/missing: package not installed"]
    assert report["review_required"]
    with pytest.raises(ValueError, match="never overwritten"):
        collect(frontend, output)

"""Collect original notices from the environment and frontend runtime packages.

Run with the Python interpreter whose packages will be redistributed.
The output is a review inventory, not an automatic legal clearance.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path


def collect(frontend: Path, output: Path, site_packages: Path | None = None) -> dict:
    if output.exists():
        raise ValueError("Output must be a new directory; existing notices are never overwritten")
    entries: list[dict] = []
    pending: list[str] = []
    output.mkdir(parents=True)
    distributions = (
        importlib.metadata.distributions(path=[str(site_packages)])
        if site_packages else importlib.metadata.distributions()
    )
    for distribution in sorted(distributions, key=lambda d: d.metadata["Name"]):
        name = distribution.metadata["Name"]
        if name.lower().replace("_", "-") == "antidupeimage-backend":
            continue
        files = []
        for relative in distribution.files or ():
            if not any(part.endswith((".dist-info", ".egg-info")) for part in relative.parts):
                continue
            if "licenses" not in relative.parts and not relative.name.upper().startswith(
                ("LICENSE", "LICENCE", "COPYING", "NOTICE", "AUTHORS", "COPYRIGHT")
            ):
                continue
            source = Path(distribution.locate_file(relative))
            destination = output / "python" / name / Path(*relative.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            content = source.read_bytes()
            destination.write_bytes(content)
            files.append({"path": destination.relative_to(output).as_posix(),
                          "sha256": hashlib.sha256(content).hexdigest()})
        if not files:
            pending.append(f"python:{name}: no original notice files in distribution metadata")
        entries.append({"ecosystem": "python", "name": name,
                         "version": distribution.version,
                         "project_urls": distribution.metadata.get_all("Project-URL") or [],
                         "home_page": distribution.metadata.get("Home-page"),
                         "license_expression": distribution.metadata.get("License-Expression"),
                        "files": files})

    lock = json.loads((frontend / "package-lock.json").read_text(encoding="utf-8"))
    packages = lock["packages"]
    for location, package in sorted(packages.items()):
        if not location or package.get("dev") or package.get("devOptional"):
            continue
        root = frontend / location
        files = []
        if not root.is_dir():
            pending.append(f"npm:{location}: package not installed")
        else:
            for source in sorted(root.iterdir()):
                if not source.is_file() or not source.name.upper().startswith(
                    ("LICENSE", "LICENCE", "COPYING", "NOTICE", "COPYRIGHT")
                ):
                    continue
                destination = output / "npm" / location / source.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                content = source.read_bytes()
                destination.write_bytes(content)
                files.append({"path": destination.relative_to(output).as_posix(),
                              "sha256": hashlib.sha256(content).hexdigest()})
            if not files:
                pending.append(f"npm:{location}: no original notice files at package root")
        entries.append({"ecosystem": "npm", "name": location,
                        "version": package["version"], "source": package.get("resolved"),
                        "license_expression": package.get("license"), "files": files})

    report = {"schema_version": 1, "packages": entries, "missing_notices": pending,
              "review_required": ["native libraries and runtime licenses",
                                  "MPL/source-availability obligations",
                                  "optional ML weights and CUDA terms",
                                  "final archive contents"]}
    (output / "inventory.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frontend", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = collect(args.frontend.resolve(), args.output.resolve())
    print(f"Packages: {len(report['packages'])}; missing notices: {len(report['missing_notices'])}")
    return 1 if report["missing_notices"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

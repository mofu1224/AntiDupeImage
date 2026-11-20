"""Assemble a Windows candidate from explicit runtime and locked production environment.

This does not download software or certify redistribution rights. Preserve
upstream runtime notices and review the resulting inventory before distribution.
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from collect_distribution_licenses import collect


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--site-packages", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    output = args.output.resolve()
    if output.exists():
        raise ValueError("Candidate output must not already exist")
    runtime = args.runtime.resolve()
    if not (runtime / "python.exe").is_file() or not (runtime / "LICENSE.txt").is_file():
        raise ValueError("A complete runtime with its original LICENSE.txt is required")
    frontend = repo / "frontend"
    if not (frontend / "dist/index.html").is_file():
        raise ValueError("Build frontend with VITE_API_BASE=/api before assembling")
    shutil.copytree(runtime, output / "runtime", ignore=shutil.ignore_patterns(
        "__pycache__", "*.pyc", "Scripts", "site-packages"
    ))
    shutil.copytree(args.site_packages, output / "runtime/Lib/site-packages",
                    ignore=shutil.ignore_patterns(
                        "__pycache__", "*.pyc", "*.egg-link", "*.whl",
                        "__editable__*", "antidupeimage_backend-*.dist-info",
                        "_virtualenv.py", "_virtualenv.pth",
                    ))
    shutil.copytree(repo / "backend/app", output / "backend/app",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copytree(frontend / "dist", output / "frontend")
    for name in ("Start.bat", "Start.ps1"):
        shutil.copyfile(repo / "portable" / name, output / name)
    shutil.copyfile(repo / "portable/README.md", output / "README.md")
    for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
        shutil.copyfile(repo / name, output / name)
    report = collect(frontend, output / "licenses", args.site_packages.resolve())
    if report["missing_notices"]:
        raise RuntimeError("Original notices are missing; candidate is not distributable")
    print("Candidate assembled; license review and isolated runtime verification still required.")


if __name__ == "__main__":
    main()

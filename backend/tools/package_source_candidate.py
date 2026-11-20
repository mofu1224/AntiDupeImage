"""Freeze the current source tree into a deterministic source ZIP, without Git writes."""
from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

ROOT_FILES = (
    'README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md', 'CHANGELOG.md',
    'SECURITY.md', 'CONTRIBUTING.md', '.env.example', '.gitignore', '.gitattributes',
    'ruff.toml', 'AntiDupeImage.bat', 'AntiDupeImage.command', 'run_windows.ps1',
    'run_windows.bat', 'setup_windows.bat', 'run_mac.sh', 'setup_mac.sh',
    'backend/README.md', 'backend/LICENSE', 'backend/pyproject.toml', 'backend/uv.lock',
    'frontend/package.json', 'frontend/package-lock.json', 'frontend/index.html',
    'frontend/tsconfig.json', 'frontend/tsconfig.e2e.json', 'frontend/vite.config.ts',
    'frontend/playwright.config.ts', 'docs/portable_clone.md',
)
SOURCE_DIRS = ('backend/app', 'backend/tests', 'backend/tools', 'frontend/src',
               'frontend/e2e', 'scripts', '.github', 'portable', 'licenses')
EXCLUDED_PARTS = {'__pycache__', 'node_modules', 'dist', 'build', '.pytest_cache'}


def source_files(root: Path) -> list[Path]:
    files = [root / name for name in ROOT_FILES]
    for directory in SOURCE_DIRS:
        parent = root / directory
        if parent.is_symlink():
            raise ValueError(f'Symbolic link in source input: {directory}')
        for path in parent.rglob('*'):
            relative = path.relative_to(root)
            if (directory != 'licenses' and EXCLUDED_PARTS.intersection(relative.parts)) or path.suffix == '.pyc':
                continue
            if any('backup' in part.lower() or part.startswith('.pytest') for part in relative.parts):
                continue
            if path.is_symlink():
                raise ValueError(f'Symbolic link in source input: {relative.as_posix()}')
            if path.is_file():
                files.append(path)
    for path in files:
        if path.is_symlink() or not path.is_file():
            raise ValueError(f'Missing or unsafe source: {path.relative_to(root).as_posix()}')
    return sorted(set(files), key=lambda p: p.relative_to(root).as_posix())


def package(root: Path, output: Path) -> dict:
    root = root.resolve()
    output = output.resolve()
    manifest_path = output.with_suffix('.manifest.json')
    if output.exists() or manifest_path.exists():
        raise ValueError('Candidate and manifest must be new files; never overwrite a fixed candidate')
    files = source_files(root)
    contents = [(path.relative_to(root).as_posix(), path.read_bytes()) for path in files]
    manifest = {'schema_version': 1, 'distribution': 'source-clone', 'files': [
        {'path': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
        for name, data in contents
    ]}
    with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in contents:
            info = zipfile.ZipInfo(f'AntiDupeImage/{name}', date_time=(2026, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (0o100755 if name.endswith(('.sh', '.command')) else 0o100644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data, compresslevel=9)
    manifest['archive_sha256'] = hashlib.sha256(output.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='New ZIP in an existing output directory')
    args = parser.parse_args()
    result = package(Path(__file__).resolve().parents[2], args.output)
    print(f"Files: {len(result['files'])}; SHA-256: {result['archive_sha256']}")


if __name__ == '__main__':
    main()

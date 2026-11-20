"""Repository-local setup and single-server launcher (stdlib bootstrap only)."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import secrets
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / '.portable'
MANIFEST = json.loads((ROOT / 'scripts/portable-runtime.json').read_text())


def local_environment() -> dict[str, str]:
    env = os.environ.copy()
    for key in ('PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV', 'UV_PROJECT_ENVIRONMENT'):
        env.pop(key, None)
    paths = {
        'UV_CACHE_DIR': LOCAL / 'cache/uv',
        'UV_PYTHON_INSTALL_DIR': LOCAL / 'python',
        'UV_PYTHON_BIN_DIR': LOCAL / 'bin',
        'UV_PROJECT_ENVIRONMENT': LOCAL / 'venv',
        'npm_config_cache': LOCAL / 'cache/npm',
        'npm_config_userconfig': LOCAL / 'npmrc',
        'npm_config_globalconfig': LOCAL / 'global-npmrc',
        'HF_HOME': LOCAL / 'cache/huggingface',
        'TORCH_HOME': LOCAL / 'cache/torch',
        'XDG_CACHE_HOME': LOCAL / 'cache',
        'TEMP': LOCAL / 'cache/temp',
        'TMP': LOCAL / 'cache/temp',
        'TMPDIR': LOCAL / 'cache/temp',
        'PLAYWRIGHT_BROWSERS_PATH': LOCAL / 'cache/playwright',
    }
    env.update({key: str(value) for key, value in paths.items()})
    env.setdefault('ANTIDUPEIMAGE_HOME', str(ROOT / 'data'))
    env.update(UV_NO_CONFIG='1', UV_PYTHON_PREFERENCE='only-managed',
               UV_LINK_MODE='copy', PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1',
               npm_config_update_notifier='false')
    Path(env['TEMP']).mkdir(parents=True, exist_ok=True)
    return env


def target() -> str:
    system = platform.system().lower()
    arch = 'arm64' if platform.machine().lower() in ('arm64', 'aarch64') else 'x64'
    value = f'{system}-{arch}'
    if value not in MANIFEST['node']:
        raise RuntimeError(f'Unsupported portable platform: {value}')
    return value


def install_node(offline: bool) -> tuple[Path, Path]:
    entry = MANIFEST['node'][target()]
    directory = LOCAL / 'tools' / entry['archive'].removesuffix('.zip').removesuffix('.tar.gz')
    node = directory / ('node.exe' if os.name == 'nt' else 'bin/node')
    npm = directory / ('node_modules/npm/bin/npm-cli.js' if os.name == 'nt'
                       else 'lib/node_modules/npm/bin/npm-cli.js')
    if node.is_file() and npm.is_file():
        return node, npm
    if offline:
        raise RuntimeError('Node.js is missing. First setup requires internet.')
    archive = LOCAL / 'cache' / entry['archive']
    url = f"https://nodejs.org/dist/v{MANIFEST['node_version']}/{entry['archive']}"
    print(f'Downloading {entry["archive"]} ...', flush=True)
    with urllib.request.urlopen(url, timeout=120) as response, archive.open('wb') as output:
        shutil.copyfileobj(response, output)
    digest = hashlib.sha256()
    with archive.open('rb') as downloaded:
        for block in iter(lambda: downloaded.read(1024 * 1024), b''):
            digest.update(block)
    actual_hash = digest.hexdigest()
    if actual_hash != entry['sha256']:
        archive.unlink()
        raise RuntimeError('Node.js archive SHA-256 mismatch.')
    tools = LOCAL / 'tools'
    tools.mkdir(exist_ok=True)
    if archive.suffix == '.zip':
        with zipfile.ZipFile(archive) as package:
            # Reject escaping archive members before extraction.
            for name in package.namelist():
                if not (tools / name).resolve().is_relative_to(tools.resolve()):
                    raise RuntimeError('Unsafe archive member')
            package.extractall(tools)
    else:
        with tarfile.open(archive) as package:
            package.extractall(tools, filter='data')
    archive.unlink()
    return node, npm


def fingerprint(profile: str) -> str:
    digest = hashlib.sha256(profile.encode())
    for relative in ('backend/pyproject.toml', 'backend/uv.lock', 'frontend/package.json',
                     'frontend/package-lock.json', 'scripts/portable.py',
                     'scripts/portable-runtime.json'):
        digest.update((ROOT / relative).read_bytes())
    for source in ('frontend/src', 'backend/app'):
        for path in sorted((ROOT / source).rglob('*')):
            if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc':
                digest.update(str(path.relative_to(ROOT)).encode())
                digest.update(path.read_bytes())
    for relative in ('frontend/index.html', 'frontend/vite.config.ts', 'frontend/tsconfig.json'):
        digest.update((ROOT / relative).read_bytes())
    return digest.hexdigest()


def run(command: list[str | Path], env: dict[str, str], cwd: Path = ROOT) -> None:
    subprocess.run([str(part) for part in command], env=env, cwd=cwd, check=True, timeout=900)


@contextmanager
def instance_lock():
    LOCAL.mkdir(parents=True, exist_ok=True)
    with (LOCAL / 'instance.lock').open('a+b') as lock:
        lock.seek(0)
        if os.name == 'nt':
            import msvcrt
            if os.fstat(lock.fileno()).st_size == 0:
                lock.write(b'0')
                lock.flush()
            lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError('This portable folder is already running or being set up.') from exc
        else:
            import fcntl
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError('This portable folder is already running or being set up.') from exc
        try:
            yield
        finally:
            if os.name == 'nt':
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def write_state(path: Path, state: dict) -> None:
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(state, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def setup(args: argparse.Namespace, env: dict[str, str]) -> Path:
    uv = LOCAL / 'tools' / f"uv-{MANIFEST['uv_version']}" / ('uv.exe' if os.name == 'nt' else 'uv')
    python = LOCAL / 'venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    state_file = LOCAL / 'state.json'
    try:
        state = json.loads(state_file.read_text(encoding='utf-8')) if state_file.exists() else {}
        if not isinstance(state, dict):
            state = {}
    except (json.JSONDecodeError, UnicodeError):
        print('Setup state is damaged; rebuilding the environment. User data is preserved.', flush=True)
        state = {}
    profile = args.profile or state.get('profile', 'base')
    wanted = {'root': str(ROOT), 'target': target(), 'fingerprint': fingerprint(profile),
              'profile': profile, 'ready': True}
    if state == wanted and python.exists() and (ROOT / 'frontend/dist/index.html').exists() and not args.setup_only:
        return python
    # Mark pending before changing dependencies; a failed forced refresh must retry.
    write_state(state_file, {**wanted, 'ready': False})
    node, npm = install_node(args.offline)
    env['PATH'] = str(node.parent) + os.pathsep + env.get('PATH', '')
    # A venv embeds absolute paths. Only our disposable portable venv is recreated.
    if state.get('root') != str(ROOT) or state.get('target') != target() or not python.exists():
        run([uv, 'venv', '--clear', '--relocatable', '--python', sys.executable,
             LOCAL / 'venv'], env)
    sync = [uv, 'sync', '--project', ROOT / 'backend', '--locked', '--no-dev',
            '--no-editable', '--python', sys.executable]
    if profile != 'base':
        sync += ['--extra', profile]
    if args.offline:
        sync += ['--offline']
    run(sync, env)
    npm_args = [node, npm, 'ci', '--no-audit', '--no-fund']
    if args.offline:
        npm_args += ['--offline']
    run(npm_args, env, ROOT / 'frontend')
    env['VITE_API_BASE'] = '/api'
    run([node, npm, 'run', 'build'], env, ROOT / 'frontend')
    run([uv, 'pip', 'check', '--python', python], env)
    write_state(state_file, wanted)
    print('Portable setup complete.', flush=True)
    return python


def launch(python: Path, env: dict[str, str]) -> None:
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    origin = f'http://127.0.0.1:{port}'
    nonce, bootstrap = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    env.update(ANTIDUPEIMAGE_LAUNCH_NONCE=nonce, ANTIDUPEIMAGE_UI_ORIGIN=origin,
               ANTIDUPEIMAGE_STATIC_DIR=str(ROOT / 'frontend/dist'))
    server = None
    # Explicit dir avoids the interpreter's cached, inherited system temp directory.
    with tempfile.TemporaryDirectory(prefix='antidupeimage-handoff.', dir=env['TEMP']) as directory:
        handoff = Path(directory) / 'handoff.html'
        if os.name == 'nt':
            identity = subprocess.check_output(['whoami', '/user', '/fo', 'csv', '/nh'], text=True)
            sid = identity.strip().split(',')[-1].strip('"')
            run(['icacls', directory, '/inheritance:r', '/grant:r', f'*{sid}:(OI)(CI)F'], env)
        else:
            Path(directory).chmod(0o700)
        handoff.write_text('<!doctype html><meta charset="utf-8">'
                           '<meta name="referrer" content="no-referrer">'
                           '<meta http-equiv="Cache-Control" content="no-store">'
                           f'<meta name="antidupeimage-bootstrap" content="{bootstrap}">'
                           f'<script>location.replace("{origin}/#bootstrap={bootstrap}");</script>')
        handoff.chmod(0o600)
        env['ANTIDUPEIMAGE_HANDOFF_PATH'] = str(handoff)
        try:
            server = subprocess.Popen([str(python), '-I', '-B', '-m', 'uvicorn', 'app.main:app',
                                       '--app-dir', str(ROOT / 'backend'), '--host', '127.0.0.1',
                                       '--port', str(port), '--no-access-log'], env=env, cwd=ROOT)
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                if server.poll() is not None:
                    raise RuntimeError('Server exited before startup.')
                request = urllib.request.Request(origin + '/_antidupeimage/launch-health',
                                                  headers={'X-AntiDupeImage-Launch-Nonce': nonce})
                try:
                    with urllib.request.urlopen(request, timeout=1) as response:
                        if json.load(response).get('nonce') == nonce:
                            break
                except (OSError, urllib.error.URLError):
                    pass
                time.sleep(0.1)
            else:
                raise RuntimeError('Server failed launch ownership check.')
            print(f'UI: {origin}', flush=True)
            print('Stop: Ctrl+C', flush=True)
            if env.get('ANTIDUPEIMAGE_NO_BROWSER') != '1':
                if os.name == 'nt':
                    os.startfile(str(handoff))
                else:
                    run(['open', handoff], env)
            code = server.wait()
            if code:
                raise RuntimeError(f'Server stopped with exit code {code}')
        finally:
            if server is not None and server.poll() is None:
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--setup-only', action='store_true')
    parser.add_argument('--offline', action='store_true', help='Use only previously downloaded files')
    parser.add_argument('--ml', action='store_const', const='ml', dest='profile', help='Install optional CLIP')
    parser.add_argument('--cuda', action='store_const', const='ml-cu130', dest='profile', help='Install CUDA 13.0 CLIP')
    parser.add_argument('legacy', nargs='?', choices=('cpu', 'setup'))
    args = parser.parse_args()
    if args.legacy == 'cpu':
        args.profile = 'ml'
    if args.legacy == 'setup':
        args.setup_only = True
    if args.profile == 'ml-cu130' and os.name != 'nt':
        parser.error('--cuda is supported on Windows x64 only')
    env = local_environment()
    with instance_lock():
        python = setup(args, env)
        if not args.setup_only:
            launch(python, env)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(f'Portable launcher: {exc}', file=sys.stderr)
        sys.exit(1)

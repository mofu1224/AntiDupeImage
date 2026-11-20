from __future__ import annotations

import importlib.util
import argparse
import json
import os
import re
import signal
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('portable_launcher', ROOT / 'scripts/portable.py')
assert spec and spec.loader
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


def test_portable_environment_contains_caches_and_ignores_python_injection(monkeypatch):
    monkeypatch.setenv('PYTHONPATH', 'untrusted')
    monkeypatch.setenv('PYTHONHOME', 'untrusted')
    env = launcher.local_environment()
    assert 'PYTHONPATH' not in env and 'PYTHONHOME' not in env
    for key in ('UV_CACHE_DIR', 'UV_PYTHON_INSTALL_DIR', 'UV_PROJECT_ENVIRONMENT',
                'npm_config_cache', 'HF_HOME', 'TORCH_HOME', 'TMP', 'TEMP', 'TMPDIR'):
        assert Path(env[key]).is_relative_to(ROOT / '.portable')


def test_mac_bootstrap_pins_match_manifest():
    manifest = launcher.MANIFEST
    shell = (ROOT / 'scripts/bootstrap.sh').read_text()
    assert f"UV_VERSION={manifest['uv_version']}" in shell
    assert f"PYTHON_VERSION={manifest['python_version']}" in shell
    for target in ('darwin-arm64', 'darwin-x64'):
        assert manifest['uv'][target]['sha256'] in shell


def test_node_hash_mismatch_is_rejected(tmp_path, monkeypatch):
    import io
    monkeypatch.setattr(launcher, 'LOCAL', tmp_path)
    monkeypatch.setattr(launcher, 'target', lambda: 'windows-x64')
    (tmp_path / 'cache').mkdir()
    monkeypatch.setattr(launcher.urllib.request, 'urlopen', lambda *a, **k: io.BytesIO(b'bad archive'))
    with pytest.raises(RuntimeError, match='SHA-256 mismatch'):
        launcher.install_node(False)
    assert not any((tmp_path / 'cache').iterdir())


def test_missing_node_offline_fails_without_network(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, 'LOCAL', tmp_path)
    monkeypatch.setattr(launcher, 'target', lambda: 'windows-x64')
    with pytest.raises(RuntimeError, match='First setup requires internet'):
        launcher.install_node(True)


def test_failed_refresh_remains_pending_and_preserves_ml_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, 'LOCAL', tmp_path)
    monkeypatch.setattr(launcher, 'target', lambda: 'windows-x64')
    monkeypatch.setattr(launcher, 'fingerprint', lambda profile: 'new-fingerprint')
    state_file = tmp_path / 'state.json'
    state_file.write_text(json.dumps({'profile': 'ml', 'ready': True}))

    def fail_install(offline):
        raise RuntimeError('download interrupted')

    monkeypatch.setattr(launcher, 'install_node', fail_install)
    with pytest.raises(RuntimeError, match='download interrupted'):
        launcher.setup(argparse.Namespace(profile=None, offline=False, setup_only=True), {})
    state = json.loads(state_file.read_text())
    assert state['ready'] is False
    assert state['profile'] == 'ml'


def test_corrupt_setup_state_does_not_prevent_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, 'LOCAL', tmp_path)
    monkeypatch.setattr(launcher, 'target', lambda: 'windows-x64')
    monkeypatch.setattr(launcher, 'fingerprint', lambda profile: 'current')
    (tmp_path / 'state.json').write_text('{interrupted')

    def failed_download(offline):
        raise RuntimeError('download interrupted')

    monkeypatch.setattr(launcher, 'install_node', failed_download)
    with pytest.raises(RuntimeError, match='download interrupted'):
        launcher.setup(argparse.Namespace(profile=None, offline=False, setup_only=True), {})
    assert json.loads((tmp_path / 'state.json').read_text())['ready'] is False


def test_concurrent_launch_is_rejected_and_lock_released(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, 'LOCAL', tmp_path)
    with launcher.instance_lock():
        with pytest.raises(RuntimeError, match='already running'):
            with launcher.instance_lock():
                pytest.fail('Concurrent startup must not modify the shared environment')
    with launcher.instance_lock():
        assert (tmp_path / 'instance.lock').exists()


def request_json(url, *, payload=None, headers=None):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data,
                                     headers={'Content-Type': 'application/json', **(headers or {})})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


@pytest.mark.skipif(launcher.platform.system() not in ('Windows', 'Darwin')
                    or os.getenv('ADI_PORTABLE_SMOKE') != '1',
                    reason='Opt-in Windows/macOS cold-download and relocation smoke test')
def test_cold_clone_move_offline_and_authenticated_scan(tmp_path):
    clone = tmp_path / 'clone'
    clone.mkdir()
    for name in ('backend', 'frontend', 'scripts'):
        shutil.copytree(ROOT / name, clone / name, ignore=shutil.ignore_patterns(
            '.venv', 'node_modules', 'dist', 'build', '*.egg-info', '__pycache__', '.pytest*',
            '*backup*', 'test-results', 'playwright-report'))
    shutil.copyfile(ROOT / 'AntiDupeImage.bat', clone / 'AntiDupeImage.bat')
    # Only OS tools: no installed Python, uv, npm or Node on PATH; no user data overrides.
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(('ANTIDUPEIMAGE_', 'UV_', 'PYTHON', 'npm_config_'))}
    if os.name == 'nt':
        windows = Path(next(value for key, value in env.items() if key.upper() == 'SYSTEMROOT'))
        env['PATH'] = os.pathsep.join(map(str, (windows / 'System32', windows,
                                               windows / 'System32/WindowsPowerShell/v1.0')))
        command = [str(windows / 'System32/WindowsPowerShell/v1.0/powershell.exe'),
                   '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File']
        script = 'scripts/bootstrap.ps1'
    else:
        env['PATH'] = '/usr/bin:/bin:/usr/sbin:/sbin'
        command = ['/bin/bash']
        script = 'scripts/bootstrap.sh'
    env['ANTIDUPEIMAGE_NO_BROWSER'] = '1'
    subprocess.run(command + [str(clone / script), '--setup-only'],
                   env=env, cwd=tmp_path, check=True, timeout=240)
    (clone / 'data').mkdir()
    (clone / 'data/keep.txt').write_text('preserved user data')
    moved = tmp_path / '移動後 空白フォルダ'
    clone.rename(moved)
    subprocess.run(command + [str(moved / script), '--setup-only', '--offline'],
                   env=env, cwd=tmp_path, check=True, timeout=240)
    assert (moved / 'data/keep.txt').read_text() == 'preserved user data'
    log_path = tmp_path / 'startup.log'
    with log_path.open('w') as log:
        process = subprocess.Popen(command + [str(moved / script), '--offline'],
                                   env=env, cwd=tmp_path, stdout=log, stderr=log,
                                   start_new_session=os.name != 'nt')
        try:
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                text = log_path.read_text()
                match = re.search(r'UI: (http://127\.0\.0\.1:\d+)', text)
                if match:
                    break
                assert process.poll() is None, text
                time.sleep(.2)
            else:
                pytest.fail(log_path.read_text())
            origin = match.group(1)
            with urllib.request.urlopen(origin) as response:
                assert b'AntiDupeImage' in response.read()
            with pytest.raises(urllib.error.HTTPError) as error:
                request_json(origin + '/api/system')
            assert error.value.code == 401
            handoff = next((moved / '.portable/cache/temp').glob('antidupeimage-handoff.*/handoff.html'))
            bootstrap = re.search(r'content="([A-Za-z0-9_-]{40,128})"', handoff.read_text()).group(1)
            exchanged = request_json(origin + '/_antidupeimage/bootstrap', payload={}, headers={
                'Origin': origin, 'X-AntiDupeImage-Bootstrap': bootstrap})
            assert not handoff.exists()
            with pytest.raises(urllib.error.HTTPError):
                request_json(origin + '/_antidupeimage/bootstrap', payload={}, headers={
                    'Origin': origin, 'X-AntiDupeImage-Bootstrap': bootstrap})
            auth = {'X-AntiDupeImage-Token': exchanged['api_token']}
            pictures = moved / 'test-pictures'
            pictures.mkdir()
            for name in ('a.png', 'b.png'):
                Image.new('RGB', (48, 48), 'red').save(pictures / name)
            scan = request_json(origin + '/api/scans', payload={
                'path': str(pictures), 'thresholds': {
                    'precision_mode': 'exact_pixel', 'clip_enabled': False}}, headers=auth)
            scan_id = scan['id']
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                result = request_json(origin + f'/api/scans/{scan_id}', headers=auth)
                if result['status'] in ('completed', 'failed'):
                    break
                time.sleep(.1)
            assert result['status'] == 'completed', result
            groups = request_json(origin + f'/api/scans/{scan_id}/group-summaries', headers=auth)
            assert groups['total'] == 1
            group_id = groups['groups'][0]['id']
            detail = request_json(origin + f'/api/scans/{scan_id}/groups/{group_id}', headers=auth)
            images = detail['images']
            with pytest.raises(urllib.error.HTTPError) as error:
                request_json(origin + '/api/files/move-duplicates', payload={
                    'scan_id': scan_id, 'group_id': group_id,
                    'image_ids': [image['id'] for image in images], 'confirm': True}, headers=auth)
            assert error.value.code == 400
            selected = next(image['id'] for image in images
                            if image['id'] != detail['recommended_keep_id'])
            moved_result = request_json(origin + '/api/files/move-duplicates', payload={
                'scan_id': scan_id, 'group_id': group_id,
                'image_ids': [selected], 'confirm': True}, headers=auth)
            assert moved_result['moved'] == [selected]
            assert len(list(pictures.glob('*.png'))) == 1
            assert len(list(Path(moved_result['destination_folder']).glob('*.png'))) == 1
            assert (moved / 'data/antidupeimage.sqlite3').exists()
            assert not (clone / 'data').exists()
        finally:
            if os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                               capture_output=True, check=False)
            else:
                os.killpg(process.pid, signal.SIGINT)
            process.wait(timeout=10)
            shutil.rmtree(moved)

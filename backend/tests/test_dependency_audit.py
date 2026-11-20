from __future__ import annotations

import io
import json
import sys

import pytest

if sys.version_info < (3, 11):
    pytest.skip('The standalone OSV tool requires Python 3.11+', allow_module_level=True)

from tools.audit_locked_dependencies import audit


def test_advisory_is_reported_and_partial_response_is_rejected(tmp_path, monkeypatch):
    lock = tmp_path / 'uv.lock'
    lock.write_text('[[package]]\nname = "example"\nversion = "1.0"\nsource = {registry = "https://pypi.org/simple"}\n')
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **k: io.BytesIO(json.dumps({
        'results': [{'vulns': [{'id': 'GHSA-example'}]}]
    }).encode()))
    assert audit(lock)['findings'] == [
        {'name': 'example', 'version': '1.0', 'advisories': ['GHSA-example']}
    ]
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **k: io.BytesIO(b'{"results": []}'))
    with pytest.raises(ValueError, match='Incomplete'):
        audit(lock)

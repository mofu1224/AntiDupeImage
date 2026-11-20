"""Query OSV for every registry package/version in uv.lock (Python 3.11+)."""
from __future__ import annotations

import json
import sys
import tomllib
import urllib.error
import urllib.request
from pathlib import Path


def audit(lock_path: Path) -> dict:
    lock = tomllib.loads(lock_path.read_text(encoding='utf-8'))
    packages = [p for p in lock['package'] if 'registry' in p.get('source', {})]
    payload = {'queries': [
        {'package': {'name': p['name'], 'ecosystem': 'PyPI'}, 'version': p['version']}
        for p in packages
    ]}
    request = urllib.request.Request('https://api.osv.dev/v1/querybatch',
                                     data=json.dumps(payload).encode(),
                                     headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=60) as response:
        results = json.load(response)['results']
    if len(results) != len(packages):
        raise ValueError('Incomplete OSV response; audit did not complete')
    return {'checked_packages': len(packages), 'findings': [
        {'name': package['name'], 'version': package['version'],
         'advisories': [v['id'] for v in result.get('vulns', [])]}
        for package, result in zip(packages, results) if result.get('vulns')
    ]}


def main() -> int:
    try:
        report = audit(Path(__file__).resolve().parents[1] / 'uv.lock')
    except (OSError, ValueError, KeyError, urllib.error.URLError):
        print('Dependency audit could not complete. Check connectivity and OSV availability.', file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2))
    return 1 if report['findings'] else 0


if __name__ == '__main__':
    raise SystemExit(main())

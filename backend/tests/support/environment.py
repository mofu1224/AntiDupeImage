from __future__ import annotations

import os
import secrets
import tempfile
from collections.abc import MutableMapping
from pathlib import Path


APPLICATION_ENV_PREFIX = "ANTIDUPEIMAGE_"


def configure_test_environment(
    environment: MutableMapping[str, str],
    test_home: Path,
) -> None:
    """Give tests exclusive ownership of all application settings."""
    for name in list(environment):
        if name.upper().startswith(APPLICATION_ENV_PREFIX):
            environment.pop(name, None)
    environment["ANTIDUPEIMAGE_HOME"] = str(test_home)
    environment["ANTIDUPEIMAGE_API_TOKEN"] = "test-token"


def create_test_temp_root(repository_root: Path) -> Path:
    """Create an isolated writable root without trusting stale repository ACLs."""
    repository_temp_root = repository_root / ".pytest-tmp"
    candidates = (repository_temp_root, Path(tempfile.gettempdir()))
    for parent in candidates:
        try:
            parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            continue
        if not _can_create_file(parent):
            continue
        return Path(tempfile.mkdtemp(prefix="antidupeimage-tests-", dir=str(parent)))
    raise RuntimeError("No writable temporary directory is available for backend tests")


def _can_create_file(directory: Path) -> bool:
    probe = directory / f".antidupeimage-write-probe-{os.getpid()}-{secrets.token_hex(8)}"
    try:
        with probe.open("x", encoding="utf-8"):
            pass
    except OSError:
        return False
    finally:
        try:
            probe.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            return False
    return True

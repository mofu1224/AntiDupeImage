from __future__ import annotations

from pathlib import Path

from tests.support.environment import configure_test_environment


def test_configure_test_environment_replaces_inherited_application_settings(
    tmp_path: Path,
) -> None:
    environment = {
        "PATH": "keep-me",
        "ANTIDUPEIMAGE_DB": "parent.sqlite3",
        "antidupeimage_home": "parent-home",
        "ANTIDUPEIMAGE_MAX_REQUEST_BODY_BYTES": "1",
    }

    configure_test_environment(environment, tmp_path)

    assert environment["PATH"] == "keep-me"
    assert environment["ANTIDUPEIMAGE_HOME"] == str(tmp_path)
    assert environment["ANTIDUPEIMAGE_API_TOKEN"] == "test-token"
    assert set(name.upper() for name in environment) == {
        "PATH",
        "ANTIDUPEIMAGE_HOME",
        "ANTIDUPEIMAGE_API_TOKEN",
    }

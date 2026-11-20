from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from tests.support.environment import configure_test_environment, create_test_temp_root


_repo_root = Path(__file__).resolve().parents[1]
_pytest_tmp_root = create_test_temp_root(_repo_root)
tempfile.tempdir = str(_pytest_tmp_root)
_pytest_base_temp = _pytest_tmp_root / "pytest"
for var in ("TMP", "TEMP", "TMPDIR"):
    os.environ[var] = str(_pytest_tmp_root)

_test_home = _pytest_tmp_root / "app-home"
_test_home.mkdir()
configure_test_environment(os.environ, _test_home)


def pytest_configure(config: pytest.Config) -> None:
    if config.getoption("basetemp") is None:
        config.option.basetemp = str(_pytest_base_temp)

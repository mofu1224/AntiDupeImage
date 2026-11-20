from __future__ import annotations

import re
from pathlib import Path

_repo_root = Path(__file__).resolve().parents[2]

# Batch helpers (npm, npx, yarn, pnpm) are implemented as .cmd/.bat batch
# scripts. Invoking one from a parent batch script without `call` replaces the
# parent's subroutine context, so later `call :label` lines fail with
# "The system cannot find the batch label specified". The Windows launcher must
# therefore always use `call` for these commands.
_BATCH_HELPERS = re.compile(r"^\s*(npm|npx|yarn|pnpm)(\.cmd)?\b", re.IGNORECASE)
_CALL_PREFIX = re.compile(r"^\s*call\b", re.IGNORECASE)


def test_windows_launcher_invokes_batch_helpers_with_call() -> None:
    launcher = _repo_root / "AntiDupeImage.bat"
    lines = launcher.read_text(encoding="utf-8").splitlines()
    offenders = [
        f"{index + 1}: {line}"
        for index, line in enumerate(lines)
        if _BATCH_HELPERS.match(line) and not _CALL_PREFIX.match(line)
    ]
    assert not offenders, (
        "AntiDupeImage.bat invokes a batch-implemented helper without `call`, "
        "which breaks the remaining setup steps:\n" + "\n".join(offenders)
    )

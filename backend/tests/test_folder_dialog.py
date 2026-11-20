from __future__ import annotations

import subprocess

import pytest

from app.services import folder_dialog


def test_windows_folder_picker_keeps_initial_path_out_of_powershell_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    initial_path = r"C:\photos; Write-Output should-not-run"

    monkeypatch.setattr(folder_dialog.shutil, "which", lambda _: "powershell.exe")

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        captured["env"] = kwargs["env"]
        return subprocess.CompletedProcess(command, 0, stdout=initial_path, stderr="")

    monkeypatch.setattr(folder_dialog.subprocess, "run", fake_run)

    assert folder_dialog._select_folder_windows(initial_path) == initial_path
    command = captured["command"]
    assert isinstance(command, list)
    assert initial_path not in command
    assert command[-2] == "-Command"
    env = captured["env"]
    assert isinstance(env, dict)
    assert env["ANTIDUPEIMAGE_INITIAL_FOLDER"] == initial_path

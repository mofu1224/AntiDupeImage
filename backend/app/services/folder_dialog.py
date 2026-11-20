from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path


class FolderDialogUnavailable(RuntimeError):
    pass


def select_scan_folder(initial_path: str | None = None) -> str | None:
    initial = _initial_directory(initial_path)
    system = platform.system()
    if system == "Darwin":
        return _select_folder_macos(initial)
    if system == "Windows":
        return _select_folder_windows(initial)
    return _select_folder_tk(initial)


def _initial_directory(value: str | None) -> str:
    if not value:
        return ""
    try:
        path = Path(value).expanduser()
        if path.is_file():
            path = path.parent
        if path.is_dir():
            return str(path.resolve())
    except OSError:
        return ""
    return ""


def _select_folder_macos(initial_path: str) -> str | None:
    script = """
on run argv
  set initialPath to item 1 of argv
  if initialPath is "" then
    set selectedFolder to choose folder with prompt "Choose a folder to scan"
  else
    set selectedFolder to choose folder with prompt "Choose a folder to scan" default location (POSIX file initialPath)
  end if
  return POSIX path of selectedFolder
end run
"""
    try:
        result = subprocess.run(
            ["osascript", "-e", script, initial_path],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise FolderDialogUnavailable("macOS folder picker is unavailable") from exc
    return _selected_path_from_process(result)


def _select_folder_windows(initial_path: str) -> str | None:
    executable = shutil.which("powershell.exe") or shutil.which("powershell") or shutil.which("pwsh")
    if executable is None:
        raise FolderDialogUnavailable("Windows folder picker requires PowerShell")
    script = r"""
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = "Choose a folder to scan"
$dialog.ShowNewFolderButton = $false
if ($env:ANTIDUPEIMAGE_INITIAL_FOLDER) {
  $dialog.SelectedPath = $env:ANTIDUPEIMAGE_INITIAL_FOLDER
}
if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
  [Console]::Out.WriteLine($dialog.SelectedPath)
}
"""
    result = subprocess.run(
        [executable, "-NoProfile", "-STA", "-Command", script],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "ANTIDUPEIMAGE_INITIAL_FOLDER": initial_path},
    )
    return _selected_path_from_process(result)


def _select_folder_tk(initial_path: str) -> str | None:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:
        raise FolderDialogUnavailable("Folder picker is unavailable in this environment") from exc

    root = tk.Tk()
    root.withdraw()
    try:
        selected = filedialog.askdirectory(
            parent=root,
            initialdir=initial_path or None,
            title="Choose a folder to scan",
            mustexist=True,
        )
    finally:
        root.destroy()
    return selected or None


def _selected_path_from_process(result: subprocess.CompletedProcess[str]) -> str | None:
    selected = result.stdout.strip()
    if selected:
        return selected
    stderr = result.stderr.lower()
    if result.returncode != 0 and "cancel" not in stderr:
        raise FolderDialogUnavailable(result.stderr.strip() or "Folder picker failed")
    return None

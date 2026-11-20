from __future__ import annotations

from app.services.file_actions import (
    SnapshotFileStatus,
    file_operation_lock,
    snapshot_file_status,
    validate_move_selection,
)


trash_operation_lock = file_operation_lock
validate_trash_selection = validate_move_selection

__all__ = [
    "SnapshotFileStatus",
    "snapshot_file_status",
    "trash_operation_lock",
    "validate_trash_selection",
]

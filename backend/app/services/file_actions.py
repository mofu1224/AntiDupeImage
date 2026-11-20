from __future__ import annotations

import errno
import os
import shutil
import stat
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.models.schemas import ImageStatus
from app.services.features import (
    FileIdentity,
    extract_pixel_digest,
    file_identity_matches_snapshot,
    get_file_identity,
)


DUPLICATE_FOLDER_PREFIX = "AntiDupeImage_Duplicates_"
file_operation_lock = threading.RLock()
_DIRECTORY_FD_SUPPORTED = (
    os.name != "nt"
    and os.link in os.supports_dir_fd
    and os.stat in os.supports_dir_fd
    and os.unlink in os.supports_dir_fd
)
_ROLLBACK_PARENT_CHANGED_ERROR = (
    "original parent changed during rollback; the staged file was retained"
)
_DIRECTORY_IDENTITY_LOSS_ERRNOS = {errno.ENOENT, errno.ENOTDIR, errno.ELOOP}


class _DirectoryIdentityChangedError(ValueError):
    """The captured directory path no longer resolves to the same directory."""


@dataclass(frozen=True)
class SnapshotFileStatus:
    status: ImageStatus
    available: bool
    reason: str | None = None


@dataclass(frozen=True)
class DuplicateMoveRoot:
    path: Path
    file_dev: int
    file_ino: int

    def __str__(self) -> str:
        return str(self.path)


@dataclass(frozen=True)
class DirectoryIdentity:
    path: Path
    file_dev: int
    file_ino: int


@dataclass(frozen=True)
class ClaimedMove:
    path: Path
    file_dev: int
    file_ino: int
    mtime_ns: int
    file_size: int
    staging_path: Path
    staging_root: DuplicateMoveRoot
    source_parent: DirectoryIdentity


def validate_move_selection(
    member_ids: set[int],
    selected_ids: set[int],
    available_ids: set[int] | None = None,
) -> None:
    if not selected_ids:
        return
    if not selected_ids.issubset(member_ids):
        raise ValueError("Selection contains images outside the group")
    live_ids = member_ids if available_ids is None else available_ids
    selected_live_ids = selected_ids & live_ids
    if selected_live_ids and selected_live_ids == live_ids:
        raise ValueError("Refusing to move every available image in a group")


def create_duplicate_move_root(scan_path: str) -> DuplicateMoveRoot:
    base_dir = _move_base_dir(scan_path)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = f"{DUPLICATE_FOLDER_PREFIX}{timestamp}"
    for index in range(1, 1000):
        suffix = "" if index == 1 else f"_{index}"
        candidate = base_dir / f"{base_name}{suffix}"
        try:
            candidate.mkdir(mode=0o700)
        except FileExistsError:
            continue
        metadata = candidate.lstat()
        return DuplicateMoveRoot(
            path=candidate,
            file_dev=int(getattr(metadata, "st_dev", 0) or 0),
            file_ino=int(getattr(metadata, "st_ino", 0) or 0),
        )
    raise FileExistsError(f"Unable to create a unique duplicate folder in {base_dir}")


def move_snapshot_to_duplicate_folder(
    snapshot: dict[str, Any],
    scan_path: str,
    destination_root: DuplicateMoveRoot | Path,
) -> Path:
    claim = prepare_snapshot_move_to_duplicate_folder(snapshot, scan_path, destination_root)
    try:
        finalize_claimed_move(snapshot, destination_root, claim)
    except Exception as exc:
        rollback_error = rollback_claimed_move(snapshot, scan_path, claim)
        if rollback_error is not None:
            raise RuntimeError(
                f"Moved file finalization failed and rollback failed: {exc}; {rollback_error}"
            ) from exc
        raise RuntimeError(f"Moved file finalization failed and was restored: {exc}") from exc
    return claim.path


def prepare_snapshot_move_to_duplicate_folder(
    snapshot: dict[str, Any],
    scan_path: str,
    destination_root: DuplicateMoveRoot | Path,
) -> ClaimedMove:
    source = Path(snapshot["path"])
    root = _coerce_move_root(destination_root)
    _validate_move_root(root)
    current_status = snapshot_file_status(snapshot, scan_path, verify_digest=True)
    if not current_status.available:
        raise ValueError(current_status.reason or current_status.status)
    _validate_same_filesystem(source, root.path)
    source_parent = _validate_source_parent(source, scan_path)
    relative_path = _relative_snapshot_path(source, scan_path)
    destination = root.path / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    _validate_destination_parent(destination.parent, root)
    _validate_move_root(root)
    staging_path, staging_root = _create_staging_path(root, source.name)
    moved_path: Path | None = None
    try:
        _verify_link_capability(staging_root, source_parent)
        shutil.move(str(source), str(staging_path))
        _validate_move_root(root)
        _validate_move_root(staging_root)
        _validate_moved_snapshot(snapshot, staging_path, root)
        destination_parent = _validate_destination_parent(destination.parent, root)
        moved_path = _claim_unique_destination(staging_path, destination, destination_parent)
        _validate_move_root(root)
        moved_identity = _validate_moved_snapshot(snapshot, moved_path, root)
    except Exception as exc:
        rollback_error = _rollback_move(
            staging_path,
            source,
            snapshot,
            scan_path,
            source_parent=source_parent,
            require_snapshot_match=False,
        )
        if rollback_error is None and moved_path is not None:
            _unlink_if_same_file_if_present(moved_path, source)
        if rollback_error is None:
            rollback_error = _cleanup_staging_after_rollback(
                staging_path,
                source,
                snapshot,
                scan_path,
            )
        if rollback_error is not None:
            raise RuntimeError(
                f"Moved file failed integrity checks and rollback failed: {exc}; {rollback_error}"
            ) from exc
        raise RuntimeError(f"Moved file failed integrity checks and was restored: {exc}") from exc
    finally:
        _remove_empty_staging_directory(staging_root)
    return ClaimedMove(
        path=moved_path,
        file_dev=int(moved_identity.file_dev),
        file_ino=int(moved_identity.file_ino),
        mtime_ns=int(moved_identity.mtime_ns),
        file_size=int(moved_identity.file_size),
        staging_path=staging_path,
        staging_root=staging_root,
        source_parent=source_parent,
    )


def revalidate_claimed_move(
    snapshot: dict[str, Any],
    destination_root: DuplicateMoveRoot | Path,
    claim: ClaimedMove,
) -> None:
    root = _coerce_move_root(destination_root)
    _validate_move_root(root)
    _validate_destination_parent(claim.path.parent, root)
    identity = _validate_moved_snapshot(snapshot, claim.path, root)
    if (
        int(identity.file_dev),
        int(identity.file_ino),
        int(identity.mtime_ns),
        int(identity.file_size),
    ) != (
        claim.file_dev,
        claim.file_ino,
        claim.mtime_ns,
        claim.file_size,
    ):
        raise ValueError("Claimed destination identity changed before database update")
    _assert_same_file(claim.staging_path, claim.path)


def finalize_claimed_move(
    snapshot: dict[str, Any],
    destination_root: DuplicateMoveRoot | Path,
    claim: ClaimedMove,
    *,
    verify_destination: bool = True,
) -> None:
    if verify_destination:
        revalidate_claimed_move(snapshot, destination_root, claim)
    _unlink_if_same_file(claim.staging_path, claim.path)
    _remove_empty_staging_directory(claim.staging_root)


def rollback_claimed_move(
    snapshot: dict[str, Any],
    scan_path: str,
    claim: ClaimedMove,
) -> str | None:
    rollback_error = _rollback_move(
        claim.staging_path,
        Path(snapshot["path"]),
        snapshot,
        scan_path,
        source_parent=claim.source_parent,
    )
    if rollback_error is None:
        _unlink_if_same_file_if_present(claim.path, Path(snapshot["path"]))
    _remove_empty_staging_directory(claim.staging_root)
    return rollback_error


def restore_snapshot_from_duplicate_folder(
    snapshot: dict[str, Any],
    scan_path: str,
    moved_path: Path,
    destination_root: DuplicateMoveRoot | Path,
) -> None:
    """Restore a moved snapshot without overwriting a newly-created source file."""
    root = _coerce_move_root(destination_root)
    _validate_move_root(root)
    _validate_moved_snapshot(snapshot, moved_path, root)
    source = Path(snapshot["path"])
    source_parent = _validate_source_parent(source, scan_path)
    rollback_error = _rollback_move(
        moved_path,
        source,
        snapshot,
        scan_path,
        source_parent=source_parent,
    )
    if rollback_error is not None:
        raise RuntimeError(rollback_error)


def discard_snapshot_from_duplicate_folder(
    snapshot: dict[str, Any],
    scan_path: str,
    moved_path: Path,
    destination_root: DuplicateMoveRoot | Path,
) -> None:
    """Discard a verified duplicate-folder copy when the original is still available."""
    root = _coerce_move_root(destination_root)
    _validate_move_root(root)
    source_status = snapshot_file_status(snapshot, scan_path, verify_digest=True)
    if not source_status.available:
        raise RuntimeError(source_status.reason or source_status.status)
    _validate_moved_snapshot(snapshot, moved_path, root)
    parent = _validate_destination_parent(moved_path.parent, root)
    _unlink_from_verified_parent(moved_path, Path(snapshot["path"]), parent)


def is_duplicate_move_directory(path: Path) -> bool:
    return path.name.startswith(DUPLICATE_FOLDER_PREFIX)


def snapshot_file_status(
    snapshot: dict[str, Any],
    scan_path: str,
    *,
    verify_digest: bool,
) -> SnapshotFileStatus:
    was_moved = bool(snapshot.get("trashed"))

    path = Path(snapshot["path"])
    if not _is_in_scan_scope(path, Path(scan_path)):
        return SnapshotFileStatus("unsafe", False, "File path is outside the original scan scope")
    try:
        identity = get_file_identity(path)
    except Exception as exc:
        message = str(exc)
        status = "missing" if "no longer exists" in message else "unsafe"
        if was_moved and status == "missing":
            return SnapshotFileStatus("moved", False, "File was already moved to a duplicate folder")
        return SnapshotFileStatus(status, False, message)

    if Path(identity.path) != path:
        return SnapshotFileStatus(
            "unsafe",
            False,
            "File path now resolves through a different location",
        )
    if not file_identity_matches_snapshot(snapshot, identity):
        return SnapshotFileStatus("changed", False, "File identity changed after the scan")
    if verify_digest:
        try:
            verified_identity, digest = extract_pixel_digest(
                path,
                feature_revision=snapshot.get("feature_revision"),
            )
        except Exception as exc:
            return SnapshotFileStatus("changed", False, str(exc))
        if not file_identity_matches_snapshot(snapshot, verified_identity):
            return SnapshotFileStatus("changed", False, "File identity changed during verification")
        if digest != snapshot["pixel_digest"]:
            return SnapshotFileStatus("changed", False, "Decoded image content changed after the scan")
    return SnapshotFileStatus("available", True)


def _move_base_dir(scan_path: str) -> Path:
    root = Path(scan_path)
    base_dir = root if root.is_dir() else root.parent
    if not base_dir.exists() or not base_dir.is_dir():
        raise ValueError(f"Scan root is no longer available: {scan_path}")
    return base_dir


def _relative_snapshot_path(path: Path, scan_path: str) -> Path:
    root = Path(scan_path)
    base_dir = root if root.is_dir() else root.parent
    try:
        return path.relative_to(base_dir)
    except ValueError:
        return Path(path.name)


def _claim_unique_destination(
    staging_path: Path,
    path: Path,
    parent: DirectoryIdentity,
) -> Path:
    for index in range(1, 10000):
        candidate = (
            path
            if index == 1
            else path.with_name(f"{path.stem}_{index}{path.suffix}")
        )
        try:
            _link_no_overwrite(staging_path, candidate, parent)
        except FileExistsError:
            continue
        return candidate
    raise FileExistsError(f"Unable to create a unique destination path for {path}")


def _create_staging_path(root: DuplicateMoveRoot, source_name: str) -> tuple[Path, DuplicateMoveRoot]:
    staging_directory = Path(tempfile.mkdtemp(prefix=".antidupe-stage-", dir=root.path))
    metadata = staging_directory.lstat()
    staging_root = DuplicateMoveRoot(
        path=staging_directory,
        file_dev=int(getattr(metadata, "st_dev", 0) or 0),
        file_ino=int(getattr(metadata, "st_ino", 0) or 0),
    )
    return staging_directory / source_name, staging_root


def _validate_destination_parent(path: Path, root: DuplicateMoveRoot) -> DirectoryIdentity:
    try:
        resolved_parent = path.resolve(strict=True)
        resolved_root = root.path.resolve(strict=True)
        resolved_parent.relative_to(resolved_root)
    except (OSError, ValueError) as exc:
        raise ValueError("Duplicate destination path escaped the generated folder") from exc
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ValueError("Duplicate destination parent is not a regular directory")
    return DirectoryIdentity(path, int(metadata.st_dev), int(metadata.st_ino))


def _validate_source_parent(source: Path, scan_path: str) -> DirectoryIdentity:
    parent = source.parent
    try:
        metadata = parent.lstat()
        resolved_parent = parent.resolve(strict=True)
        scan = Path(scan_path)
        scan_scope = parent if scan.absolute() == source.absolute() else scan
        resolved_scope = scan_scope.resolve(strict=True)
        resolved_parent.relative_to(resolved_scope)
    except (OSError, ValueError) as exc:
        raise ValueError("Original file parent escaped the scan scope") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ValueError("Original file parent is not a regular directory")
    if resolved_parent != parent.absolute():
        raise ValueError("Original file parent now resolves through a different location")
    return DirectoryIdentity(parent, int(metadata.st_dev), int(metadata.st_ino))


def _validate_same_filesystem(source: Path, destination_root: Path) -> None:
    try:
        source_device = int(source.lstat().st_dev)
        destination_device = int(destination_root.lstat().st_dev)
    except OSError as exc:
        raise ValueError("Unable to verify the move filesystem") from exc
    if source_device != destination_device:
        raise ValueError(
            "Refusing a cross-filesystem move because it cannot be rolled back atomically"
        )


def _verify_link_capability(
    staging_root: DuplicateMoveRoot,
    source_parent: DirectoryIdentity,
) -> None:
    probe_source = staging_root.path / ".link-capability-probe"
    probe_target = source_parent.path / f".antidupe-link-probe-{os.urandom(12).hex()}"
    descriptor = os.open(probe_source, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    try:
        _link_no_overwrite(probe_source, probe_target, source_parent)
        _unlink_if_same_file(probe_target, probe_source)
    except Exception as exc:
        raise ValueError(f"Atomic duplicate move is unavailable on this filesystem: {exc}") from exc
    finally:
        _unlink_if_same_file_if_present(probe_target, probe_source)
        try:
            probe_source.unlink()
        except FileNotFoundError:
            pass


def _directory_fd_supported() -> bool:
    return _DIRECTORY_FD_SUPPORTED


def _open_verified_directory(directory: DirectoryIdentity) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(directory.path, flags)
    except OSError as exc:
        if exc.errno not in _DIRECTORY_IDENTITY_LOSS_ERRNOS:
            raise
        raise _DirectoryIdentityChangedError(
            f"Directory identity changed: {directory.path}"
        ) from exc
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or int(metadata.st_dev) != directory.file_dev
        or int(metadata.st_ino) != directory.file_ino
    ):
        os.close(descriptor)
        raise _DirectoryIdentityChangedError(
            f"Directory identity changed: {directory.path}"
        )
    return descriptor


def _validate_directory_identity(directory: DirectoryIdentity) -> None:
    try:
        metadata = directory.path.lstat()
    except OSError as exc:
        if exc.errno not in _DIRECTORY_IDENTITY_LOSS_ERRNOS:
            raise
        raise _DirectoryIdentityChangedError(
            f"Directory identity changed: {directory.path}"
        ) from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or int(metadata.st_dev) != directory.file_dev
        or int(metadata.st_ino) != directory.file_ino
    ):
        raise _DirectoryIdentityChangedError(
            f"Directory identity changed: {directory.path}"
        )


def _directory_path_matches_fd(directory: DirectoryIdentity, descriptor: int) -> bool:
    try:
        path_metadata = directory.path.lstat()
    except OSError as exc:
        if exc.errno not in _DIRECTORY_IDENTITY_LOSS_ERRNOS:
            raise
        return False
    descriptor_metadata = os.fstat(descriptor)
    return (
        not stat.S_ISLNK(path_metadata.st_mode)
        and stat.S_ISDIR(path_metadata.st_mode)
        and int(path_metadata.st_dev) == int(descriptor_metadata.st_dev)
        and int(path_metadata.st_ino) == int(descriptor_metadata.st_ino)
    )


def _link_no_overwrite(source: Path, destination: Path, parent: DirectoryIdentity) -> None:
    if _directory_fd_supported():
        descriptor = _open_verified_directory(parent)
        created = False
        try:
            os.link(
                source,
                destination.name,
                dst_dir_fd=descriptor,
                follow_symlinks=False,
            )
            created = True
            source_metadata = source.lstat()
            target_metadata = os.stat(destination.name, dir_fd=descriptor, follow_symlinks=False)
            if (
                int(source_metadata.st_dev),
                int(source_metadata.st_ino),
            ) != (
                int(target_metadata.st_dev),
                int(target_metadata.st_ino),
            ) or not _directory_path_matches_fd(parent, descriptor):
                raise ValueError("Linked destination or parent identity changed")
        except Exception:
            if created:
                _unlink_dirfd_if_same_file(descriptor, destination.name, source)
            raise
        finally:
            os.close(descriptor)
        return

    _validate_directory_identity(parent)
    os.link(source, destination, follow_symlinks=False)
    try:
        _validate_directory_identity(parent)
        _assert_same_file(destination, source)
    except Exception:
        _unlink_if_same_file_if_present(destination, source)
        raise


def _unlink_dirfd_if_same_file(descriptor: int, name: str, expected_peer: Path) -> None:
    try:
        target_metadata = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        peer_metadata = expected_peer.lstat()
    except FileNotFoundError:
        return
    if (
        int(target_metadata.st_dev),
        int(target_metadata.st_ino),
    ) != (
        int(peer_metadata.st_dev),
        int(peer_metadata.st_ino),
    ):
        return
    os.unlink(name, dir_fd=descriptor)


def _unlink_from_verified_parent(
    path: Path,
    expected_peer: Path,
    parent: DirectoryIdentity,
) -> None:
    if _directory_fd_supported():
        descriptor = _open_verified_directory(parent)
        try:
            target_metadata = os.stat(path.name, dir_fd=descriptor, follow_symlinks=False)
            peer_metadata = expected_peer.lstat()
            if (
                int(target_metadata.st_dev),
                int(target_metadata.st_ino),
            ) != (
                int(peer_metadata.st_dev),
                int(peer_metadata.st_ino),
            ):
                raise ValueError(f"File identity changed: {path}")
            if not _directory_path_matches_fd(parent, descriptor):
                raise ValueError(f"Directory identity changed: {parent.path}")
            os.unlink(path.name, dir_fd=descriptor)
        finally:
            os.close(descriptor)
        return
    _validate_directory_identity(parent)
    _unlink_if_same_file(path, expected_peer)


def _coerce_move_root(destination_root: DuplicateMoveRoot | Path) -> DuplicateMoveRoot:
    if isinstance(destination_root, DuplicateMoveRoot):
        return destination_root
    path = Path(destination_root)
    metadata = path.lstat()
    return DuplicateMoveRoot(
        path=path,
        file_dev=int(getattr(metadata, "st_dev", 0) or 0),
        file_ino=int(getattr(metadata, "st_ino", 0) or 0),
    )


def _validate_move_root(root: DuplicateMoveRoot) -> None:
    try:
        metadata = root.path.lstat()
        resolved = root.path.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"Duplicate destination folder is no longer available: {root.path}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ValueError(f"Duplicate destination folder is not a regular directory: {root.path}")
    if resolved != root.path.absolute():
        raise ValueError(f"Duplicate destination folder now resolves elsewhere: {root.path}")
    if root.file_dev and int(getattr(metadata, "st_dev", 0) or 0) != root.file_dev:
        raise ValueError(f"Duplicate destination folder identity changed: {root.path}")
    if root.file_ino and int(getattr(metadata, "st_ino", 0) or 0) != root.file_ino:
        raise ValueError(f"Duplicate destination folder identity changed: {root.path}")


def _validate_moved_snapshot(
    snapshot: dict[str, Any],
    moved_path: Path,
    root: DuplicateMoveRoot,
) -> FileIdentity:
    try:
        resolved_moved = moved_path.resolve(strict=True)
        resolved_root = root.path.resolve(strict=True)
        resolved_moved.relative_to(resolved_root)
    except (OSError, ValueError) as exc:
        raise ValueError("Moved file escaped the generated duplicate folder") from exc
    moved_identity, digest = extract_pixel_digest(
        moved_path,
        feature_revision=snapshot.get("feature_revision"),
    )
    if not file_identity_matches_snapshot(snapshot, moved_identity):
        raise ValueError("Moved file identity does not match the scan snapshot")
    if digest != snapshot["pixel_digest"]:
        raise ValueError("Moved decoded image content does not match the scan snapshot")
    return moved_identity


def _rollback_move(
    moved_path: Path,
    source: Path,
    snapshot: dict[str, Any],
    scan_path: str,
    *,
    source_parent: DirectoryIdentity | None = None,
    require_snapshot_match: bool = True,
) -> str | None:
    try:
        if not moved_path.exists():
            status = snapshot_file_status(snapshot, scan_path, verify_digest=True)
            return None if status.available else f"moved file is unavailable: {moved_path}"
        if source.exists():
            status = snapshot_file_status(snapshot, scan_path, verify_digest=True)
            if not status.available:
                return f"original path is already occupied by a different file: {source}"
            _unlink_verified_snapshot_copy(moved_path, snapshot)
            return None
        verified_parent = source_parent or _validate_source_parent(source, scan_path)
        restore_error = _restore_link(
            moved_path,
            source,
            verified_parent,
            snapshot,
            scan_path,
            require_snapshot_match=require_snapshot_match,
        )
        if restore_error is not None:
            return restore_error
    except Exception as exc:
        return str(exc)
    return None


def _restore_link(
    moved_path: Path,
    source: Path,
    source_parent: DirectoryIdentity,
    snapshot: dict[str, Any],
    scan_path: str,
    *,
    require_snapshot_match: bool,
) -> str | None:
    if _directory_fd_supported():
        try:
            descriptor = _open_verified_directory(source_parent)
        except _DirectoryIdentityChangedError:
            return _ROLLBACK_PARENT_CHANGED_ERROR
        except OSError as exc:
            return f"unable to atomically restore the original file: {exc}"
        created = False
        try:
            os.link(
                moved_path,
                source.name,
                dst_dir_fd=descriptor,
                follow_symlinks=False,
            )
            created = True
            target_metadata = os.stat(source.name, dir_fd=descriptor, follow_symlinks=False)
            moved_metadata = moved_path.lstat()
            same_file = (
                int(target_metadata.st_dev),
                int(target_metadata.st_ino),
            ) == (
                int(moved_metadata.st_dev),
                int(moved_metadata.st_ino),
            )
            status = snapshot_file_status(snapshot, scan_path, verify_digest=True)
            parent_stable = _directory_path_matches_fd(source_parent, descriptor)
            if not same_file or not parent_stable or (require_snapshot_match and not status.available):
                _unlink_dirfd_if_same_file(descriptor, source.name, moved_path)
                if not parent_stable:
                    return _ROLLBACK_PARENT_CHANGED_ERROR
                return f"restored original failed snapshot verification: {status.reason or status.status}"
            _unlink_if_same_file(moved_path, source)
        except FileExistsError:
            return f"original path became occupied during rollback: {source}"
        except Exception as exc:
            if created:
                _unlink_dirfd_if_same_file(descriptor, source.name, moved_path)
            return f"unable to atomically restore the original file: {exc}"
        finally:
            os.close(descriptor)
        return None

    try:
        _validate_directory_identity(source_parent)
    except _DirectoryIdentityChangedError:
        return _ROLLBACK_PARENT_CHANGED_ERROR
    except OSError as exc:
        return f"unable to atomically restore the original file: {exc}"

    try:
        os.link(moved_path, source, follow_symlinks=False)
        status = snapshot_file_status(snapshot, scan_path, verify_digest=True)
        try:
            _validate_directory_identity(source_parent)
        except _DirectoryIdentityChangedError:
            _unlink_if_same_file_if_present(source, moved_path)
            return _ROLLBACK_PARENT_CHANGED_ERROR
        if require_snapshot_match and not status.available:
            _unlink_if_same_file_if_present(source, moved_path)
            return f"restored original failed snapshot verification: {status.reason or status.status}"
        _unlink_if_same_file(moved_path, source)
    except FileExistsError:
        return f"original path became occupied during rollback: {source}"
    except Exception as exc:
        _unlink_if_same_file_if_present(source, moved_path)
        return f"unable to atomically restore the original file: {exc}"
    return None


def _unlink_verified_snapshot_copy(path: Path, snapshot: dict[str, Any]) -> None:
    identity, digest = extract_pixel_digest(
        path,
        feature_revision=snapshot.get("feature_revision"),
    )
    if int(identity.file_size) != int(snapshot["file_size"]) or digest != snapshot["pixel_digest"]:
        raise ValueError("Duplicate-folder copy does not match the scan snapshot")
    path.unlink()


def _unlink_if_same_file(path: Path, expected_peer: Path) -> None:
    _assert_same_file(path, expected_peer)
    path.unlink()


def _assert_same_file(path: Path, expected_peer: Path) -> None:
    path_metadata = path.lstat()
    peer_metadata = expected_peer.lstat()
    if (
        int(path_metadata.st_dev),
        int(path_metadata.st_ino),
    ) != (
        int(peer_metadata.st_dev),
        int(peer_metadata.st_ino),
    ):
        raise ValueError(f"File identity changed: {path}")


def _unlink_if_same_file_if_present(path: Path, expected_peer: Path) -> None:
    try:
        _unlink_if_same_file(path, expected_peer)
    except FileNotFoundError:
        return
    except ValueError:
        # A different file won the path after our claim. It is not ours to remove.
        return


def _remove_empty_staging_directory(staging_root: DuplicateMoveRoot) -> None:
    try:
        _validate_move_root(staging_root)
        staging_root.path.rmdir()
    except (OSError, ValueError):
        return


def _cleanup_staging_after_rollback(
    staging_path: Path,
    source: Path,
    snapshot: dict[str, Any],
    scan_path: str,
) -> str | None:
    try:
        if not staging_path.exists():
            return None
        status = snapshot_file_status(snapshot, scan_path, verify_digest=True)
        if not status.available:
            return f"original file is not valid after rollback: {status.reason or status.status}"
        _unlink_verified_snapshot_copy(staging_path, snapshot)
    except Exception as exc:
        return str(exc)
    return None


def _is_in_scan_scope(path: Path, scan_path: Path) -> bool:
    try:
        resolved_path = path.resolve(strict=False)
        resolved_scan_path = scan_path.resolve(strict=False)
    except OSError:
        return False
    if resolved_path == resolved_scan_path:
        return True
    scan_scope = resolved_scan_path if not scan_path.is_file() else resolved_scan_path.parent
    try:
        resolved_path.relative_to(scan_scope)
        return True
    except ValueError:
        return False

from pathlib import Path

import pytest

Image = pytest.importorskip("PIL.Image")

from app.services.features import (
    extract_image_features,
    extract_pixel_digest,
    render_verified_snapshot_rgb,
)
from app.services import file_actions
from app.services.file_actions import (
    create_duplicate_move_root,
    move_snapshot_to_duplicate_folder,
    restore_snapshot_from_duplicate_folder,
    snapshot_file_status,
    validate_move_selection,
)


def test_rejects_moving_every_available_group_member() -> None:
    with pytest.raises(ValueError):
        validate_move_selection({1, 2, 3}, {1, 2}, {1, 2})


def test_accepts_partial_available_group_selection() -> None:
    validate_move_selection({1, 2, 3}, {2}, {1, 2})


def test_snapshot_status_detects_changed_file(tmp_path: Path) -> None:
    path = tmp_path / "image.png"
    Image.new("RGB", (20, 20), (10, 20, 30)).save(path)
    snapshot = extract_image_features(path)

    assert snapshot_file_status(snapshot, str(tmp_path), verify_digest=True).available

    Image.new("RGB", (20, 20), (30, 20, 10)).save(path)

    status = snapshot_file_status(snapshot, str(tmp_path), verify_digest=True)
    assert status.status == "changed"
    assert not status.available


def test_snapshot_status_allows_verified_file_restored_after_move(tmp_path: Path) -> None:
    path = tmp_path / "image.png"
    Image.new("RGB", (20, 20), (10, 20, 30)).save(path)
    snapshot = extract_image_features(path)
    snapshot["trashed"] = True

    status = snapshot_file_status(snapshot, str(tmp_path), verify_digest=True)

    assert status.status == "available"
    assert status.available


@pytest.mark.parametrize(
    "snapshot_revision",
    ["rgba-frames-exif-checkerboard-v4", ""],
    ids=["named-v2-digest", "schema-v2-blank-revision"],
)
def test_legacy_frame_digest_snapshot_can_be_rendered_and_moved(
    tmp_path: Path,
    snapshot_revision: str,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    path = root / "image.png"
    Image.new("RGB", (20, 20), (10, 20, 30)).save(path)
    snapshot = extract_image_features(path)
    legacy_revision = "rgba-frames-exif-checkerboard-v4"
    snapshot["feature_revision"] = snapshot_revision
    snapshot["pixel_digest"] = extract_pixel_digest(
        path,
        feature_revision=legacy_revision,
    )[1]

    status = snapshot_file_status(snapshot, str(root), verify_digest=True)
    rendered = render_verified_snapshot_rgb(snapshot)
    destination_root = create_duplicate_move_root(str(root))
    moved_path = move_snapshot_to_duplicate_folder(snapshot, str(root), destination_root)

    assert status.available
    assert rendered.getpixel((0, 0)) == (10, 20, 30)
    assert moved_path.is_file()


def test_snapshot_status_rejects_unknown_digest_revision(tmp_path: Path) -> None:
    path = tmp_path / "image.png"
    Image.new("RGB", (20, 20), (10, 20, 30)).save(path)
    snapshot = extract_image_features(path)
    snapshot["feature_revision"] = "rgba-frames-playback-exif-checkerboard-v999"

    status = snapshot_file_status(snapshot, str(tmp_path), verify_digest=True)

    assert status.status == "changed"
    assert not status.available
    assert "Unsupported feature revision" in (status.reason or "")


def test_snapshot_status_reports_moved_file_when_original_is_missing(tmp_path: Path) -> None:
    path = tmp_path / "image.png"
    Image.new("RGB", (20, 20), (10, 20, 30)).save(path)
    snapshot = extract_image_features(path)
    snapshot["trashed"] = True
    path.unlink()

    status = snapshot_file_status(snapshot, str(tmp_path), verify_digest=False)

    assert status.status == "moved"
    assert not status.available


def test_snapshot_status_rejects_out_of_scope_path(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.png"
    Image.new("RGB", (20, 20), (10, 20, 30)).save(outside)
    snapshot = extract_image_features(outside)

    status = snapshot_file_status(snapshot, str(root), verify_digest=False)

    assert status.status == "unsafe"
    assert not status.available


def test_snapshot_status_rejects_parent_directory_replaced_by_symlink(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    path = root / "image.png"
    Image.new("RGB", (20, 20), (10, 20, 30)).save(path)
    snapshot = extract_image_features(path)
    moved_root = tmp_path / "moved-root"
    root.rename(moved_root)
    try:
        root.symlink_to(moved_root, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Symbolic links are unavailable in this test environment: {exc}")

    status = snapshot_file_status(snapshot, str(root), verify_digest=True)

    assert status.status == "unsafe"
    assert not status.available


def test_move_rolls_back_when_source_changes_after_final_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    source = root / "image.png"
    Image.new("RGB", (20, 20), (10, 20, 30)).save(source)
    snapshot = extract_image_features(source)
    destination_root = create_duplicate_move_root(str(root))
    original_move = file_actions.shutil.move
    replaced = False

    def replace_then_move(source_path: str, destination_path: str) -> str:
        nonlocal replaced
        if not replaced and Path(source_path) == source:
            replaced = True
            Image.new("RGB", (20, 20), (90, 80, 70)).save(source)
        return original_move(source_path, destination_path)

    monkeypatch.setattr(file_actions.shutil, "move", replace_then_move)

    with pytest.raises(RuntimeError, match="failed integrity checks and was restored"):
        move_snapshot_to_duplicate_folder(snapshot, str(root), destination_root)

    assert source.is_file()
    assert extract_image_features(source)["pixel_digest"] != snapshot["pixel_digest"]
    assert list(destination_root.path.rglob("*.png")) == []


def test_move_rolls_back_when_destination_root_is_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    source = root / "image.png"
    Image.new("RGB", (20, 20), (10, 20, 30)).save(source)
    snapshot = extract_image_features(source)
    destination_root = create_duplicate_move_root(str(root))
    original_root = destination_root.path.with_name(f"{destination_root.path.name}_original")
    outside = tmp_path / "outside"
    outside.mkdir()
    original_move = file_actions.shutil.move
    replaced = False

    def redirect_then_move(source_path: str, destination_path: str) -> str:
        nonlocal replaced
        if not replaced and Path(source_path) == source:
            replaced = True
            destination_root.path.rename(original_root)
            destination_root.path.symlink_to(outside, target_is_directory=True)
        return original_move(source_path, destination_path)

    probe_link = tmp_path / "probe-link"
    try:
        probe_link.symlink_to(outside, target_is_directory=True)
        probe_link.unlink()
    except OSError as exc:
        pytest.skip(f"Symbolic links are unavailable in this test environment: {exc}")
    monkeypatch.setattr(file_actions.shutil, "move", redirect_then_move)

    with pytest.raises(RuntimeError, match="failed integrity checks and was restored"):
        move_snapshot_to_duplicate_folder(snapshot, str(root), destination_root)

    assert source.is_file()
    assert list(outside.rglob("*.png")) == []


def test_move_does_not_overwrite_destination_created_during_move(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    source = root / "image.png"
    Image.new("RGB", (20, 20), (10, 20, 30)).save(source)
    snapshot = extract_image_features(source)
    destination_root = create_duplicate_move_root(str(root))
    raced_destination = destination_root.path / source.name
    external_bytes = b"external file that must not be overwritten"
    original_move = file_actions.shutil.move

    def create_collision_then_move(source_path: str, destination_path: str) -> str:
        raced_destination.write_bytes(external_bytes)
        return original_move(source_path, destination_path)

    monkeypatch.setattr(file_actions.shutil, "move", create_collision_then_move)

    moved_path = move_snapshot_to_duplicate_folder(snapshot, str(root), destination_root)

    assert raced_destination.read_bytes() == external_bytes
    assert moved_path == destination_root.path / "image_2.png"
    assert extract_image_features(moved_path)["pixel_digest"] == snapshot["pixel_digest"]


def test_move_cleans_partial_destination_copy_when_shutil_move_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    source = root / "image.png"
    Image.new("RGB", (20, 20), (10, 20, 30)).save(source)
    snapshot = extract_image_features(source)
    destination_root = create_duplicate_move_root(str(root))

    def copy_then_fail(source_path: str, destination_path: str) -> str:
        file_actions.shutil.copy2(source_path, destination_path)
        raise OSError("simulated partial move failure")

    monkeypatch.setattr(file_actions.shutil, "move", copy_then_fail)

    with pytest.raises(RuntimeError, match="failed integrity checks and was restored"):
        move_snapshot_to_duplicate_folder(snapshot, str(root), destination_root)

    assert extract_image_features(source)["pixel_digest"] == snapshot["pixel_digest"]
    assert list(destination_root.path.rglob("*.png")) == []


def test_move_proves_hard_link_support_before_mutating_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    source = root / "image.png"
    Image.new("RGB", (20, 20), (10, 20, 30)).save(source)
    snapshot = extract_image_features(source)
    destination_root = create_duplicate_move_root(str(root))
    move_calls = 0

    def unsupported_link(*_args: object, **_kwargs: object) -> None:
        raise OSError("hard links unsupported")

    def unexpected_move(_source_path: str, _destination_path: str) -> str:
        nonlocal move_calls
        move_calls += 1
        raise AssertionError("source must not move before the link capability probe")

    monkeypatch.setattr(file_actions.os, "link", unsupported_link)
    monkeypatch.setattr(file_actions.shutil, "move", unexpected_move)

    with pytest.raises(RuntimeError, match="Atomic duplicate move is unavailable"):
        move_snapshot_to_duplicate_folder(snapshot, str(root), destination_root)

    assert move_calls == 0
    assert extract_image_features(source)["pixel_digest"] == snapshot["pixel_digest"]
    assert list(destination_root.path.rglob("*.png")) == []


def test_restore_does_not_link_through_swapped_source_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    album = root / "album"
    album.mkdir(parents=True)
    source = album / "image.png"
    Image.new("RGB", (20, 20), (10, 20, 30)).save(source)
    snapshot = extract_image_features(source)
    destination_root = create_duplicate_move_root(str(root))
    moved_path = move_snapshot_to_duplicate_folder(snapshot, str(root), destination_root)
    original_album = root / "album-original"
    outside = tmp_path / "outside"
    outside.mkdir()
    original_link = file_actions.os.link
    swapped = False

    probe_link = tmp_path / "probe-link"
    try:
        probe_link.symlink_to(outside, target_is_directory=True)
        probe_link.unlink()
    except OSError as exc:
        pytest.skip(f"Symbolic links are unavailable in this test environment: {exc}")

    def swap_parent_then_link(*args: object, **kwargs: object) -> None:
        nonlocal swapped
        if not swapped:
            swapped = True
            album.rename(original_album)
            album.symlink_to(outside, target_is_directory=True)
        original_link(*args, **kwargs)

    monkeypatch.setattr(file_actions.os, "link", swap_parent_then_link)

    with pytest.raises(RuntimeError, match="parent changed during rollback"):
        restore_snapshot_from_duplicate_folder(snapshot, str(root), moved_path, destination_root)

    assert moved_path.is_file()
    assert not (outside / "image.png").exists()
    assert not (original_album / "image.png").exists()


@pytest.mark.parametrize(
    "use_directory_fd",
    [True, False],
    ids=["dir-fd", "path"],
)
def test_restore_reports_parent_change_before_link_consistently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    use_directory_fd: bool,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    source = root / "image.png"
    Image.new("RGB", (20, 20), (10, 20, 30)).save(source)
    snapshot = extract_image_features(source)
    destination_root = create_duplicate_move_root(str(root))
    moved_path = move_snapshot_to_duplicate_folder(snapshot, str(root), destination_root)
    link_calls = 0

    def reject_changed_parent(_directory: file_actions.DirectoryIdentity) -> None:
        raise file_actions._DirectoryIdentityChangedError(
            "Directory identity changed: parent"
        )

    def unexpected_link(*_args: object, **_kwargs: object) -> None:
        nonlocal link_calls
        link_calls += 1

    monkeypatch.setattr(file_actions, "_directory_fd_supported", lambda: use_directory_fd)
    if use_directory_fd:
        monkeypatch.setattr(file_actions, "_open_verified_directory", reject_changed_parent)
    else:
        monkeypatch.setattr(file_actions, "_validate_directory_identity", reject_changed_parent)
    monkeypatch.setattr(file_actions.os, "link", unexpected_link)

    expected_error = "original parent changed during rollback; the staged file was retained"
    with pytest.raises(RuntimeError) as exc_info:
        restore_snapshot_from_duplicate_folder(snapshot, str(root), moved_path, destination_root)

    assert str(exc_info.value) == expected_error
    assert link_calls == 0
    assert moved_path.is_file()
    assert not source.exists()


@pytest.mark.parametrize(
    "use_directory_fd",
    [True, False],
    ids=["dir-fd", "path"],
)
def test_restore_preserves_unrelated_os_error_before_link(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    use_directory_fd: bool,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    source = root / "image.png"
    Image.new("RGB", (20, 20), (10, 20, 30)).save(source)
    snapshot = extract_image_features(source)
    destination_root = create_duplicate_move_root(str(root))
    moved_path = move_snapshot_to_duplicate_folder(snapshot, str(root), destination_root)
    link_calls = 0

    def reject_identity_check(_directory: file_actions.DirectoryIdentity) -> None:
        raise PermissionError("simulated identity check denial")

    def unexpected_link(*_args: object, **_kwargs: object) -> None:
        nonlocal link_calls
        link_calls += 1

    monkeypatch.setattr(file_actions, "_directory_fd_supported", lambda: use_directory_fd)
    if use_directory_fd:
        monkeypatch.setattr(file_actions, "_open_verified_directory", reject_identity_check)
    else:
        monkeypatch.setattr(file_actions, "_validate_directory_identity", reject_identity_check)
    monkeypatch.setattr(file_actions.os, "link", unexpected_link)

    expected_error = (
        "unable to atomically restore the original file: "
        "simulated identity check denial"
    )
    with pytest.raises(RuntimeError) as exc_info:
        restore_snapshot_from_duplicate_folder(snapshot, str(root), moved_path, destination_root)

    assert str(exc_info.value) == expected_error
    assert link_calls == 0
    assert moved_path.is_file()
    assert not source.exists()


@pytest.mark.parametrize(
    "use_directory_fd",
    [True, False],
    ids=["dir-fd", "path"],
)
def test_restore_reports_parent_change_after_link_consistently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    use_directory_fd: bool,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    source = root / "image.png"
    Image.new("RGB", (20, 20), (10, 20, 30)).save(source)
    snapshot = extract_image_features(source)
    destination_root = create_duplicate_move_root(str(root))
    moved_path = move_snapshot_to_duplicate_folder(snapshot, str(root), destination_root)
    original_link = file_actions.os.link

    monkeypatch.setattr(file_actions, "_directory_fd_supported", lambda: use_directory_fd)
    if use_directory_fd:
        descriptor = file_actions.os.open(file_actions.os.devnull, file_actions.os.O_RDONLY)

        def open_verified_directory(_directory: file_actions.DirectoryIdentity) -> int:
            return descriptor

        def link_into_verified_parent(
            source_path: Path,
            destination_name: str,
            **_kwargs: object,
        ) -> None:
            assert destination_name == source.name
            original_link(source_path, source, follow_symlinks=False)

        def target_metadata(
            path: str | bytes | int | Path,
            *,
            dir_fd: int | None = None,
            follow_symlinks: bool = True,
        ) -> object:
            if dir_fd == descriptor and path == source.name:
                return source.lstat()
            return original_stat(path, dir_fd=dir_fd, follow_symlinks=follow_symlinks)

        def unlink_created_link(
            actual_descriptor: int,
            name: str,
            expected_peer: Path,
        ) -> None:
            assert actual_descriptor == descriptor
            assert name == source.name
            file_actions._unlink_if_same_file_if_present(source, expected_peer)

        original_stat = file_actions.os.stat
        monkeypatch.setattr(file_actions, "_open_verified_directory", open_verified_directory)
        monkeypatch.setattr(file_actions.os, "link", link_into_verified_parent)
        monkeypatch.setattr(file_actions.os, "stat", target_metadata)
        monkeypatch.setattr(file_actions, "_directory_path_matches_fd", lambda *_args: False)
        monkeypatch.setattr(file_actions, "_unlink_dirfd_if_same_file", unlink_created_link)
    else:
        original_validate = file_actions._validate_directory_identity
        validation_count = 0

        def reject_changed_parent(directory: file_actions.DirectoryIdentity) -> None:
            nonlocal validation_count
            validation_count += 1
            if validation_count == 2:
                raise file_actions._DirectoryIdentityChangedError(
                    "Directory identity changed: parent"
                )
            original_validate(directory)

        monkeypatch.setattr(file_actions, "_validate_directory_identity", reject_changed_parent)

    expected_error = "original parent changed during rollback; the staged file was retained"
    with pytest.raises(RuntimeError) as exc_info:
        restore_snapshot_from_duplicate_folder(snapshot, str(root), moved_path, destination_root)

    assert str(exc_info.value) == expected_error
    assert moved_path.is_file()
    assert not source.exists()


def test_move_rejects_same_pixel_replacement_with_new_inode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    source = root / "image.png"
    Image.new("RGB", (20, 20), (10, 20, 30)).save(source)
    snapshot = extract_image_features(source)
    destination_root = create_duplicate_move_root(str(root))
    original_move = file_actions.shutil.move
    replaced = False

    def replace_with_same_pixels_then_move(source_path: str, destination_path: str) -> str:
        nonlocal replaced
        if not replaced:
            replaced = True
            replacement = source.with_name("replacement.png")
            Image.new("RGB", (20, 20), (10, 20, 30)).save(replacement)
            file_actions.os.replace(replacement, source)
        return original_move(source_path, destination_path)

    monkeypatch.setattr(file_actions.shutil, "move", replace_with_same_pixels_then_move)

    with pytest.raises(RuntimeError, match="identity does not match"):
        move_snapshot_to_duplicate_folder(snapshot, str(root), destination_root)

    assert source.is_file()
    assert extract_image_features(source)["pixel_digest"] == snapshot["pixel_digest"]
    assert not snapshot_file_status(snapshot, str(root), verify_digest=True).available
    assert list(destination_root.path.rglob("*.png")) == []

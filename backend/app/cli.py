"""Command-line interface for AntiDupeImage.

The CLI executes the same shared operations as the WebUI backend
(``app.services.operations``); the HTTP routes in ``app.api.routes`` are
thin wrappers over those operations. WebUI requests and CLI invocations
therefore run one code path with identical validation and file-safety
checks. Only transport concerns differ (HTTP status mapping versus
process exit codes).

Usage from a repository checkout::

    uv run --project backend --extra dev python -m app.cli --help

Or, with the locked project environment on ``PATH``::

    antidupeimage --help

The CLI runs in-process against the local application database, so it
needs no API token: it executes with the current user's privileges, just
like the loopback backend. ``ANTIDUPEIMAGE_HOME`` and the other
``ANTIDUPEIMAGE_*`` environment variables are honored.

Exit codes: 0 on success, 1 on validation, operation, filesystem, or database
errors, 2 when the requested scan/group/image does not exist, 3 when waiting
for a scan times out. Keyboard interrupt exits with 130.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from pathlib import Path
from typing import Any, Callable, Sequence

from pydantic import BaseModel, ValidationError

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NOT_FOUND = 2
EXIT_TIMEOUT = 3
EXIT_INTERRUPTED = 130

try:
    from app.models.schemas import (
        GroupFilter,
        GroupSort,
        MoveDuplicatesRequest,
        PrecisionMode,
        ScanRequest,
        ScanStatus,
        Thresholds,
    )
    from app.services import operations
    from app.services.operations import OperationError
    from app.services.scan_profiles import PRECISION_THRESHOLD_PRESETS
    from app.services.scanner import scan_manager
except OSError as exc:
    print(
        f"Error: Cannot initialize the local application data directory: {exc}. "
        "Check that the data folder exists and is writable, then retry.",
        file=sys.stderr,
    )
    raise SystemExit(EXIT_ERROR) from exc

_STATUS_BY_CODE = {404: EXIT_NOT_FOUND, 504: EXIT_TIMEOUT}


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler: Callable[[argparse.Namespace], int] = args.handler
    try:
        if args.command != "serve":
            operations.get_storage().init_schema()
        return handler(args)
    except OperationError as exc:
        return fail(exc.detail, _STATUS_BY_CODE.get(exc.status_code, EXIT_ERROR))
    except ValidationError as exc:
        return fail(f"Invalid request: {exc.errors()[0]['msg']}", EXIT_ERROR)
    except (sqlite3.DatabaseError, sqlite3.OperationalError) as exc:
        return fail(
            f"Local database error: {exc}. The database file may be damaged or "
            "inaccessible; restore it from a backup or move it aside and retry.",
            EXIT_ERROR,
        )
    except OSError as exc:
        return fail(
            f"Filesystem error: {exc}. Check that the path exists and is writable, "
            "then retry.",
            EXIT_ERROR,
        )
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return EXIT_INTERRUPTED
    finally:
        scan_manager.shutdown()


def timeout_seconds(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0:
        raise argparse.ArgumentTypeError("Timeout must be a finite, non-negative number.")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="antidupeimage",
        description="Local duplicate-image review from the command line. "
        "Shares the operation layer with the WebUI backend.",
    )
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--json",
        action="store_true",
        help="Print the full response as JSON instead of human-readable text.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="command")

    system_parser = subparsers.add_parser(
        "system", parents=[common], help="Show runtime, device, and storage information."
    )
    system_parser.set_defaults(handler=run_system)

    scan_parser = subparsers.add_parser("scan", help="Start and manage scans.")
    scan_sub = scan_parser.add_subparsers(dest="scan_command", required=True)

    start_parser = scan_sub.add_parser("start", parents=[common], help="Start a scan.")
    start_parser.add_argument("path", help="Image file or folder to scan.")
    start_parser.add_argument(
        "--no-recursive", action="store_true", help="Do not descend into subfolders."
    )
    start_parser.add_argument(
        "--device",
        default="auto",
        help="Execution device: auto, cpu, mps, cuda, or cuda:<index> (default: auto).",
    )
    start_parser.add_argument("--model", default=None, help="CLIP model name override.")
    start_parser.add_argument("--pretrained", default=None, help="CLIP weights override.")
    start_parser.add_argument("--gpu-index", type=int, default=None, help="CUDA device index.")
    start_parser.add_argument(
        "--precision",
        choices=("exact_pixel", "ultra_strict", "conservative", "balanced", "broad"),
        default=None,
        help="Apply matching threshold presets like the WebUI precision modes.",
    )
    start_parser.add_argument(
        "--profile",
        choices=("standard", "lightweight"),
        default=None,
        help="Performance profile (default: standard).",
    )
    start_parser.add_argument(
        "--phash-distance", type=int, default=None, help="Override pHash distance (0-64)."
    )
    start_parser.add_argument(
        "--histogram", type=float, default=None, help="Override histogram similarity (0-1)."
    )
    start_parser.add_argument(
        "--clip-similarity", type=float, default=None, help="Override CLIP similarity (0-1)."
    )
    clip_group = start_parser.add_mutually_exclusive_group()
    clip_group.add_argument("--clip", dest="clip", action="store_true", help="Enable CLIP check.")
    clip_group.add_argument(
        "--no-clip", dest="clip", action="store_false", help="Disable CLIP check."
    )
    start_parser.set_defaults(clip=None)
    start_parser.add_argument(
        "--no-clip-fallback",
        action="store_true",
        help="Fail the scan when CLIP is unavailable instead of continuing.",
    )
    start_parser.add_argument(
        "--timeout",
        type=timeout_seconds,
        default=3600.0,
        help="Seconds to wait for completion before cancelling (default: 3600).",
    )
    start_parser.set_defaults(handler=run_scan_start)

    list_parser = scan_sub.add_parser("list", parents=[common], help="List scans.")
    list_parser.add_argument(
        "--status",
        choices=("queued", "running", "completed", "failed", "cancelled"),
        default=None,
    )
    list_parser.add_argument("--limit", type=int, default=50)
    list_parser.add_argument("--offset", type=int, default=0)
    list_parser.set_defaults(handler=run_scan_list)

    show_parser = scan_sub.add_parser("show", parents=[common], help="Show one scan.")
    show_parser.add_argument("scan_id", type=int)
    show_parser.add_argument(
        "--wait", action="store_true", help="Wait until the scan finishes."
    )
    show_parser.add_argument("--timeout", type=timeout_seconds, default=3600.0)
    show_parser.set_defaults(handler=run_scan_show)

    cancel_parser = scan_sub.add_parser("cancel", parents=[common], help="Cancel a scan.")
    cancel_parser.add_argument("scan_id", type=int)
    cancel_parser.set_defaults(handler=run_scan_cancel)

    delete_parser = scan_sub.add_parser(
        "delete", parents=[common], help="Delete a scan and its history."
    )
    delete_parser.add_argument("scan_id", type=int)
    delete_parser.set_defaults(handler=run_scan_delete)

    groups_parser = subparsers.add_parser(
        "groups", parents=[common], help="List duplicate-group summaries for a scan."
    )
    groups_parser.add_argument("scan_id", type=int)
    groups_parser.add_argument("--filter", choices=("all", "visual", "exact"), default="all")
    groups_parser.add_argument(
        "--sort", choices=("recommended", "score", "files"), default="recommended"
    )
    groups_parser.add_argument("--limit", type=int, default=100)
    groups_parser.add_argument("--offset", type=int, default=0)
    groups_parser.set_defaults(handler=run_groups)

    group_parser = subparsers.add_parser(
        "group", parents=[common], help="Show one duplicate group in detail."
    )
    group_parser.add_argument("scan_id", type=int)
    group_parser.add_argument("group_id", type=int)
    group_parser.set_defaults(handler=run_group)

    failures_parser = subparsers.add_parser(
        "failures", parents=[common], help="List per-file failures for a scan."
    )
    failures_parser.add_argument("scan_id", type=int)
    failures_parser.add_argument("--limit", type=int, default=100)
    failures_parser.add_argument("--offset", type=int, default=0)
    failures_parser.set_defaults(handler=run_failures)

    move_parser = subparsers.add_parser(
        "move",
        parents=[common],
        help="Move selected duplicates into a new duplicate folder.",
    )
    move_parser.add_argument("scan_id", type=int)
    move_parser.add_argument("group_id", type=int)
    move_parser.add_argument("image_ids", type=int, nargs="+")
    move_parser.add_argument(
        "--confirm",
        action="store_true",
        required=True,
        help="Explicit confirmation; the move is refused without it.",
    )
    move_parser.set_defaults(handler=run_move)

    thumbnail_parser = subparsers.add_parser(
        "thumbnail", parents=[common], help="Render a verified thumbnail to a JPEG file."
    )
    thumbnail_parser.add_argument("image_id", type=int)
    thumbnail_parser.add_argument("--out", type=Path, required=True)
    thumbnail_parser.set_defaults(handler=run_thumbnail)

    dialog_parser = subparsers.add_parser(
        "dialog", parents=[common], help="Open the native folder picker."
    )
    dialog_parser.add_argument("--initial-path", default=None)
    dialog_parser.set_defaults(handler=run_dialog)

    reset_parser = subparsers.add_parser(
        "reset",
        parents=[common],
        help="Clear scan history, snapshots, groups, failures, and feature caches.",
    )
    reset_parser.add_argument(
        "--confirm",
        action="store_true",
        required=True,
        help="Explicit confirmation; the reset is refused without it.",
    )
    reset_parser.set_defaults(handler=run_reset)

    serve_parser = subparsers.add_parser("serve", help="Start the loopback WebUI backend.")
    serve_parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1).")
    serve_parser.add_argument("--port", type=int, default=8000)
    serve_parser.set_defaults(handler=run_serve)

    return parser


def fail(message: str, code: int) -> int:
    print(f"Error: {message}", file=sys.stderr)
    return code


def emit(model: BaseModel, as_json: bool) -> int:
    if as_json:
        print(model.model_dump_json(indent=2))
    else:
        print_human(model)
    return EXIT_OK


def print_human(model: BaseModel) -> None:
    payload = model.model_dump(mode="json")
    name = type(model).__name__
    if name == "SystemResponse":
        device = payload["device"]
        clip = payload["clip"]
        print(f"Python: {payload['python']}")
        print(f"Platform: {payload['platform']}")
        print(f"App dir: {payload['app_dir']}")
        print(f"Database: {payload['db_path']}")
        print(f"Device: {device['selected']} (available: {', '.join(device['available'])})")
        clip_state = "available" if clip["available"] else f"unavailable ({clip['error']})"
        print(f"CLIP: {clip_state}")
    elif name == "ScanResponse":
        print(f"Scan {payload['id']}: {payload['status']}")
    elif name == "ScanState":
        print(
            f"Scan {payload['id']}: {payload['status']} ({payload['phase']}) "
            f"{payload['processed_files']}/{payload['total_files']} files, "
            f"{payload['duplicate_groups']} group(s)"
        )
        if payload.get("progress_detail"):
            print(f"Detail: {payload['progress_detail']}")
        if payload.get("warnings"):
            for warning in payload["warnings"]:
                print(f"Warning: {warning}")
        if payload.get("error"):
            print(f"Error: {payload['error']}")
    elif name == "ScanListResponse":
        print(f"Scans: {payload['total']} total (showing {len(payload['scans'])})")
        for scan in payload["scans"]:
            print(
                f"  [{scan['id']}] {scan['status']:9} {scan['duplicate_groups']:4} group(s)  {scan['path']}"
            )
    elif name == "GroupSummariesResponse":
        print(f"Scan {payload['scan_id']}: {payload['total']} group(s)")
        for group in payload["groups"]:
            print(
                f"  [group {group['id']}] {group['reason']} score={group['score']:.3f} "
                f"images={group['image_count']} keep={group['recommended_keep_path']}"
            )
    elif name == "GroupsResponse":
        print(f"Scan {payload['scan_id']}: {payload['total']} group(s)")
        for group in payload["groups"]:
            print(f"  [group {group['id']}] {group['reason']} score={group['score']:.3f}")
            for image in group["images"]:
                keep = "KEEP " if image["is_recommended_keep"] else "     "
                print(f"    {keep}[{image['id']}] {image['status']:9} {image['path']}")
    elif name == "DuplicateGroup":
        print(f"Group {payload['id']}: {payload['reason']} score={payload['score']:.3f}")
        for image in payload["images"]:
            keep = "KEEP " if image["is_recommended_keep"] else "     "
            print(f"  {keep}[{image['id']}] {image['status']:9} {image['path']}")
    elif name == "ScanFailuresResponse":
        print(f"Scan {payload['scan_id']}: {payload['total']} failure(s)")
        for failure in payload["failures"]:
            print(f"  [{failure['id']}] {failure['stage']}: {failure['path']}: {failure['error']}")
    elif name == "MoveDuplicatesResponse":
        print(f"Moved: {payload['moved']}")
        print(f"Skipped: {payload['skipped']}")
        for failure in payload["failures"]:
            print(f"  [{failure['image_id']}] {failure['reason']}")
        if payload.get("destination_folder"):
            print(f"Destination: {payload['destination_folder']}")
    elif name == "ResetAppDataResponse":
        print(json.dumps(payload, indent=2))
    elif name in ("CancelResponse", "DeleteResponse", "FolderDialogResponse"):
        print(json.dumps(payload, indent=2))
    else:  # pragma: no cover - defensive fallback
        print(json.dumps(payload, indent=2))


def run_system(args: argparse.Namespace) -> int:
    return emit(operations.get_system_info(), args.json)


def run_scan_start(args: argparse.Namespace) -> int:
    request = ScanRequest(
        path=args.path,
        recursive=not args.no_recursive,
        model=args.model,
        pretrained=args.pretrained,
        device=args.device,
        gpu_index=args.gpu_index,
        allow_clip_fallback=not args.no_clip_fallback,
        thresholds=build_thresholds(args),
    )
    response = operations.start_scan(request)
    print(f"Started scan {response.id}.", file=sys.stderr)
    try:
        state = operations.wait_for_scan(response.id, timeout_seconds=args.timeout)
    except (OperationError, KeyboardInterrupt):
        operations.cancel_scan(response.id)
        raise
    if not args.json:
        print_human(state)
        if state.status != "completed":
            return fail(f"Scan {state.id} ended with status: {state.status}", EXIT_ERROR)
        return EXIT_OK
    print(state.model_dump_json(indent=2))
    if state.status != "completed":
        return EXIT_ERROR
    return EXIT_OK


def build_thresholds(args: argparse.Namespace) -> Thresholds:
    values: dict[str, Any] = {}
    if args.precision is not None:
        mode: PrecisionMode = args.precision
        preset = PRECISION_THRESHOLD_PRESETS[mode]
        values["precision_mode"] = mode
        values["phash_distance"] = int(preset["phash_distance"])
        values["histogram_similarity"] = float(preset["histogram_similarity"])
        values["clip_similarity"] = float(preset["clip_similarity"])
    if args.profile is not None:
        values["performance_profile"] = args.profile
    if args.phash_distance is not None:
        values["phash_distance"] = args.phash_distance
    if args.histogram is not None:
        values["histogram_similarity"] = args.histogram
    if args.clip_similarity is not None:
        values["clip_similarity"] = args.clip_similarity
    if args.clip is not None:
        values["clip_enabled"] = args.clip
    return Thresholds(**values)


def run_scan_list(args: argparse.Namespace) -> int:
    status: ScanStatus | None = args.status
    return emit(operations.list_scans(args.limit, args.offset, status), args.json)


def run_scan_show(args: argparse.Namespace) -> int:
    if args.wait:
        return emit(
            operations.wait_for_scan(args.scan_id, timeout_seconds=args.timeout), args.json
        )
    return emit(operations.get_scan_state(args.scan_id), args.json)


def run_scan_cancel(args: argparse.Namespace) -> int:
    return emit(operations.cancel_scan(args.scan_id), args.json)


def run_scan_delete(args: argparse.Namespace) -> int:
    return emit(operations.delete_scan(args.scan_id), args.json)


def run_groups(args: argparse.Namespace) -> int:
    group_filter: GroupFilter = args.filter
    group_sort: GroupSort = args.sort
    return emit(
        operations.list_group_summaries(
            args.scan_id, args.limit, args.offset, group_filter, group_sort
        ),
        args.json,
    )


def run_group(args: argparse.Namespace) -> int:
    return emit(operations.get_group_detail(args.scan_id, args.group_id), args.json)


def run_failures(args: argparse.Namespace) -> int:
    return emit(operations.list_failures(args.scan_id, args.limit, args.offset), args.json)


def run_move(args: argparse.Namespace) -> int:
    request = MoveDuplicatesRequest(
        scan_id=args.scan_id,
        group_id=args.group_id,
        image_ids=list(args.image_ids),
        confirm=args.confirm,
    )
    return emit(operations.move_duplicates(request), args.json)


def run_thumbnail(args: argparse.Namespace) -> int:
    destination: Path = operations.export_thumbnail(args.image_id, args.out)
    if args.json:
        print(json.dumps({"image_id": args.image_id, "path": str(destination)}, indent=2))
    else:
        print(f"Thumbnail for image {args.image_id} written to {destination}")
    return EXIT_OK


def run_dialog(args: argparse.Namespace) -> int:
    return emit(operations.choose_scan_folder(args.initial_path), args.json)


def run_reset(args: argparse.Namespace) -> int:
    return emit(operations.reset_app_data(confirm=args.confirm), args.json)


def run_serve(args: argparse.Namespace) -> int:
    host = args.host.strip()
    if host not in {"127.0.0.1", "localhost", "::1"}:
        return fail("The backend must stay bound to a loopback address.", EXIT_ERROR)
    import uvicorn

    uvicorn.run("app.main:app", host=host, port=args.port, access_log=False)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())

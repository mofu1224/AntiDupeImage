from __future__ import annotations

import re
from pathlib import Path
from typing import get_args

from pydantic import BaseModel

from app.models import schemas


REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_TYPES = REPO_ROOT / "frontend" / "src" / "types" / "api.ts"

ENUM_ALIASES = {
    "PrecisionMode": schemas.PrecisionMode,
    "PerformanceProfile": schemas.PerformanceProfile,
    "GroupFilter": schemas.GroupFilter,
    "GroupSort": schemas.GroupSort,
    "ScanStatus": schemas.ScanStatus,
    "ScanPhase": schemas.ScanPhase,
    "ImageStatus": schemas.ImageStatus,
}

MODEL_TYPES: dict[str, type[BaseModel]] = {
    "Thresholds": schemas.Thresholds,
    "ScanRequest": schemas.ScanRequest,
    "ScanResponse": schemas.ScanResponse,
    "FolderDialogRequest": schemas.FolderDialogRequest,
    "FolderDialogResponse": schemas.FolderDialogResponse,
    "ScanState": schemas.ScanState,
    "ScanListResponse": schemas.ScanListResponse,
    "DeviceInfo": schemas.DeviceInfo,
    "ClipInfo": schemas.ClipInfo,
    "SystemResponse": schemas.SystemResponse,
    "ImageSummary": schemas.ImageSummary,
    "DuplicateGroup": schemas.DuplicateGroup,
    "GroupSummary": schemas.GroupSummary,
    "GroupsResponse": schemas.GroupsResponse,
    "GroupSummariesResponse": schemas.GroupSummariesResponse,
    "ScanFailure": schemas.ScanFailure,
    "ScanFailuresResponse": schemas.ScanFailuresResponse,
    "MoveDuplicatesRequest": schemas.MoveDuplicatesRequest,
    "MoveFailure": schemas.MoveFailure,
    "MoveDuplicatesResponse": schemas.MoveDuplicatesResponse,
    "ResetAppDataRequest": schemas.ResetAppDataRequest,
    "ResetAppDataResponse": schemas.ResetAppDataResponse,
    "DeleteResponse": schemas.DeleteResponse,
    "CancelResponse": schemas.CancelResponse,
}

RESPONSE_MODEL_TYPES = {
    "ScanResponse",
    "FolderDialogResponse",
    "ScanState",
    "ScanListResponse",
    "DeviceInfo",
    "ClipInfo",
    "SystemResponse",
    "ImageSummary",
    "DuplicateGroup",
    "GroupSummary",
    "GroupsResponse",
    "GroupSummariesResponse",
    "ScanFailure",
    "ScanFailuresResponse",
    "MoveFailure",
    "MoveDuplicatesResponse",
    "ResetAppDataResponse",
    "DeleteResponse",
    "CancelResponse",
}

TYPED_DICT_TYPES = {
    "MatchEvidence": schemas.MatchEvidence,
}


def test_frontend_literal_api_types_match_backend_literals() -> None:
    source = FRONTEND_TYPES.read_text(encoding="utf-8")
    for alias, backend_type in ENUM_ALIASES.items():
        frontend_values = _literal_alias_values(source, alias)
        backend_values = set(_literal_values(backend_type))
        assert frontend_values == backend_values, alias


def test_frontend_object_api_types_have_backend_model_fields() -> None:
    source = FRONTEND_TYPES.read_text(encoding="utf-8")
    frontend_objects = _object_type_fields_by_name(source)
    for name, model in MODEL_TYPES.items():
        assert name in frontend_objects
        assert frontend_objects[name] == set(model.model_fields), name
    for name, typed_dict in TYPED_DICT_TYPES.items():
        assert name in frontend_objects
        assert frontend_objects[name] == set(typed_dict.__annotations__), name


def test_frontend_response_api_types_do_not_mark_backend_fields_optional() -> None:
    source = FRONTEND_TYPES.read_text(encoding="utf-8")
    frontend_objects = _object_type_fields_by_name(source)
    for name in RESPONSE_MODEL_TYPES:
        assert name in frontend_objects
        optional_fields = _object_type_optional_fields(source, name)
        assert optional_fields == set(), name


def _literal_values(value: object) -> tuple[str, ...]:
    origin_args = get_args(value)
    if origin_args and all(isinstance(item, str) for item in origin_args):
        return tuple(origin_args)
    raise AssertionError(f"Expected a string Literal alias, got {value!r}")


def _literal_alias_values(source: str, alias: str) -> set[str]:
    match = re.search(rf"export\s+type\s+{re.escape(alias)}\s*=\s*(?P<body>[^;]+);", source)
    assert match is not None, f"Missing exported literal alias {alias}"
    values = set(re.findall(r'"([^"]+)"', match.group("body")))
    assert values, f"Exported literal alias {alias} has no string members"
    return values


def _object_type_fields_by_name(source: str) -> dict[str, set[str]]:
    objects: dict[str, set[str]] = {}
    for match in re.finditer(r"export\s+type\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*\{", source):
        name = match.group(1)
        body_start = match.end() - 1
        body, end_index = _balanced_brace_body(source, body_start)
        if source[end_index : end_index + 1] != ";":
            continue
        objects[name] = _top_level_object_fields(body)
    return objects


def _object_type_optional_fields(source: str, name: str) -> set[str]:
    match = re.search(rf"export\s+type\s+{re.escape(name)}\s*=\s*\{{", source)
    assert match is not None, f"Missing exported object type {name}"
    body_start = match.end() - 1
    body, _ = _balanced_brace_body(source, body_start)
    return _top_level_object_optional_fields(body)


def _balanced_brace_body(source: str, open_brace_index: int) -> tuple[str, int]:
    depth = 0
    for index in range(open_brace_index, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[open_brace_index + 1 : index], index + 1
    raise AssertionError("Unterminated object type body")


def _top_level_object_fields(body: str) -> set[str]:
    fields: set[str] = set()
    depth = 0
    statement_start = 0
    for index, char in enumerate(body):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        elif char == ";" and depth == 0:
            statement = body[statement_start:index].strip()
            statement_start = index + 1
            match = re.match(r"([A-Za-z_][A-Za-z0-9_]*)(?:\?)?\s*:", statement)
            if match:
                fields.add(match.group(1))
    return fields


def _top_level_object_optional_fields(body: str) -> set[str]:
    fields: set[str] = set()
    depth = 0
    statement_start = 0
    for index, char in enumerate(body):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        elif char == ";" and depth == 0:
            statement = body[statement_start:index].strip()
            statement_start = index + 1
            match = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\?\s*:", statement)
            if match:
                fields.add(match.group(1))
    return fields

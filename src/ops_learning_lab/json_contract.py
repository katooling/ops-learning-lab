"""Strict JSON decoding for persisted domain contracts."""

from __future__ import annotations

import json
from typing import Any, NoReturn


class JsonContractError(ValueError):
    """Raised when bytes are not strict, unambiguous JSON."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise JsonContractError(f"duplicate JSON object key: {key}")
        value[key] = item
    return value


def _reject_non_finite(value: str) -> NoReturn:
    raise JsonContractError(f"non-finite JSON number: {value}")


def decode_json_object(data: bytes, label: str) -> dict[str, Any]:
    """Decode one UTF-8 JSON object without duplicate keys or NaN values."""

    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_non_finite,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        JsonContractError,
        TypeError,
        ValueError,
    ) as exc:
        raise JsonContractError(f"{label} is not strict JSON") from exc
    if not isinstance(value, dict):
        raise JsonContractError(f"{label} must be a JSON object")
    return value

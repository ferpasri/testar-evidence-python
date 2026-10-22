"""Strict JSON support that keeps integer and decimal readings exact."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from decimal import Decimal, DecimalException
import json
import math
from typing import Any

from .errors import EvidenceError

MAX_DEPTH = 64
MAX_STRING_LENGTH = 16 * 1024 * 1024


def _invalid(message: str) -> EvidenceError:
    return EvidenceError("invalid_record", message)


def _depth_check(text: str) -> None:
    depth = 0
    quoted = False
    escaped = False
    for character in text:
        if quoted:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                quoted = False
        elif character == '"':
            quoted = True
        elif character in "[{":
            depth += 1
            if depth > MAX_DEPTH:
                raise _invalid(f"JSON nesting exceeds {MAX_DEPTH} containers")
        elif character in "]}":
            depth -= 1


def loads(text: str) -> Any:
    """Read JSON without duplicate keys, nonfinite numbers, or deep nesting."""
    if not isinstance(text, str):
        raise _invalid("JSON input must be text")
    _depth_check(text)

    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for name, value in values:
            if name in result:
                raise _invalid(f"Duplicate JSON field: {name}")
            result[name] = value
        return result

    def constant(value: str) -> None:
        raise _invalid(f"JSON numbers must be finite: {value}")

    def integer(value: str) -> int:
        negative = value.startswith("-")
        digits = value[1:] if negative else value
        result = 0
        for offset in range(0, len(digits), 1000):
            chunk = digits[offset:offset + 1000]
            result = result * (10 ** len(chunk)) + int(chunk)
        return -result if negative else result

    try:
        result = json.loads(text, parse_float=Decimal, parse_int=integer,
                            parse_constant=constant, object_pairs_hook=pairs)
        return to_data(result)
    except (ValueError, DecimalException, RecursionError) as error:
        if isinstance(error, EvidenceError):
            raise
        raise _invalid(f"Invalid JSON: {error}") from error


def to_data(value: Any) -> Any:
    """Copy JSON values, converting record objects and tuple arrays to wire data."""
    active: set[int] = set()

    def convert(current: Any, depth: int) -> Any:
        if depth > MAX_DEPTH:
            raise _invalid(f"JSON nesting exceeds {MAX_DEPTH} containers")
        if isinstance(current, str):
            if len(current) > MAX_STRING_LENGTH:
                raise _invalid("JSON string exceeds the maximum length")
            if any(0xD800 <= ord(character) <= 0xDFFF for character in current):
                raise _invalid("JSON strings cannot contain unpaired Unicode surrogates")
            return current
        if current is None or isinstance(current, (bool, int)):
            return current
        if isinstance(current, Decimal):
            if not current.is_finite():
                raise _invalid("JSON numbers must be finite")
            return current
        if isinstance(current, float):
            if not math.isfinite(current):
                raise _invalid("JSON numbers must be finite")
            return Decimal(repr(current))
        identity = id(current)
        if identity in active:
            raise _invalid("JSON values cannot contain cycles")
        active.add(identity)
        try:
            if hasattr(current, "to_dict"):
                return convert(current.to_dict(), depth)
            if is_dataclass(current) and not isinstance(current, type):
                return convert({field.name: getattr(current, field.name) for field in fields(current)}, depth)
            if isinstance(current, dict):
                if depth >= MAX_DEPTH:
                    raise _invalid(f"JSON nesting exceeds {MAX_DEPTH} containers")
                if not all(isinstance(key, str) for key in current):
                    raise _invalid("JSON object keys must be text")
                return {convert(key, depth + 1): convert(item, depth + 1) for key, item in current.items()}
            if isinstance(current, (list, tuple)):
                if depth >= MAX_DEPTH:
                    raise _invalid(f"JSON nesting exceeds {MAX_DEPTH} containers")
                return [convert(item, depth + 1) for item in current]
            raise _invalid(f"Unsupported JSON value: {type(current).__name__}")
        finally:
            active.remove(identity)

    return convert(value, 0)


def dumps(value: Any, *, sort_keys: bool = False) -> str:
    """Write compact JSON, preserving exact decimal and integer values."""
    def encode(current: Any) -> str:
        if current is None:
            return "null"
        if current is True:
            return "true"
        if current is False:
            return "false"
        if isinstance(current, str):
            return json.dumps(current, ensure_ascii=True)
        if isinstance(current, int):
            try:
                return str(current)
            except ValueError:
                return str(Decimal(current))
        if isinstance(current, Decimal):
            return str(current)
        if isinstance(current, list):
            return "[" + ",".join(encode(item) for item in current) + "]"
        keys = sorted(current) if sort_keys else current
        return "{" + ",".join(encode(key) + ":" + encode(current[key]) for key in keys) + "}"

    return encode(to_data(value))

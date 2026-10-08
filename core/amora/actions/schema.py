from __future__ import annotations

from typing import Any


def validate_schema(value: Any, schema: dict[str, Any], *, path: str = "$", depth: int = 0) -> list[str]:
    """Validate the JSON-schema subset used by V0.7 plugin contracts.

    Supported keywords: type, required, properties, items, enum and additionalProperties=false.
    Unknown keywords are ignored intentionally; plugins must not assume a full JSON Schema engine.
    """
    if not schema:
        return []
    if depth > 16:
        return [f"{path}: schema nesting limit exceeded"]

    errors: list[str] = []
    expected = schema.get("type")
    if expected is not None and not _matches_type(value, expected):
        return [f"{path}: expected {expected}"]

    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        errors.append(f"{path}: value not in enum")

    if isinstance(value, dict):
        required = schema.get("required", [])
        if isinstance(required, list):
            for key in required:
                if isinstance(key, str) and key not in value:
                    errors.append(f"{path}.{key}: required")
        properties = schema.get("properties", {})
        if isinstance(properties, dict):
            for key, child_schema in properties.items():
                if key in value and isinstance(child_schema, dict):
                    errors.extend(validate_schema(value[key], child_schema, path=f"{path}.{key}", depth=depth + 1))
            if schema.get("additionalProperties") is False:
                for key in value:
                    if key not in properties:
                        errors.append(f"{path}.{key}: additional property not allowed")

    if isinstance(value, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                errors.extend(validate_schema(item, item_schema, path=f"{path}[{index}]", depth=depth + 1))
    return errors


def _matches_type(value: Any, expected: Any) -> bool:
    if isinstance(expected, list):
        return any(_matches_type(value, item) for item in expected)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "null":
        return value is None
    return True

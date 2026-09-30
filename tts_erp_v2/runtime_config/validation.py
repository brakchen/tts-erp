"""Small, explicit JSON Schema subset for runtime configuration payloads."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


class ConfigValidationError(ValueError):
    """A payload or schema violates the supported runtime-config contract."""


_SUPPORTED_SCHEMA_KEYS = {
    "$schema",
    "additionalProperties",
    "description",
    "enum",
    "format",
    "items",
    "maxItems",
    "maxLength",
    "maximum",
    "minItems",
    "minLength",
    "minimum",
    "properties",
    "required",
    "title",
    "type",
}
_SUPPORTED_TYPES = {"array", "boolean", "integer", "null", "number", "object", "string"}


def validate_schema(schema: Any, *, path: str = "$") -> dict[str, Any]:
    """Validate the documented JSON Schema subset before storing it."""
    if not isinstance(schema, dict):
        raise ConfigValidationError(f"{path}: schema must be an object")
    unsupported = set(schema) - _SUPPORTED_SCHEMA_KEYS
    if unsupported:
        names = ", ".join(sorted(unsupported))
        raise ConfigValidationError(f"{path}: unsupported schema keywords: {names}")
    schema_type = schema.get("type")
    if schema_type is not None and schema_type not in _SUPPORTED_TYPES:
        raise ConfigValidationError(f"{path}: unsupported type {schema_type!r}")
    if "enum" in schema and not isinstance(schema["enum"], list):
        raise ConfigValidationError(f"{path}: enum must be an array")
    if "format" in schema and schema["format"] != "secret-reference":
        raise ConfigValidationError(f"{path}: unsupported format {schema['format']!r}")
    if "additionalProperties" in schema and not isinstance(
        schema["additionalProperties"], bool
    ):
        raise ConfigValidationError(f"{path}: additionalProperties must be boolean")
    if "required" in schema:
        required = schema["required"]
        if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
            raise ConfigValidationError(f"{path}: required must be an array of property names")
    if "properties" in schema:
        properties = schema["properties"]
        if not isinstance(properties, dict):
            raise ConfigValidationError(f"{path}: properties must be an object")
        for key, child in properties.items():
            if not isinstance(key, str):
                raise ConfigValidationError(f"{path}: property names must be strings")
            validate_schema(child, path=f"{path}.properties.{key}")
    if "items" in schema:
        validate_schema(schema["items"], path=f"{path}.items")
    for key in ("minimum", "maximum"):
        if key in schema and (not isinstance(schema[key], (int, float)) or isinstance(schema[key], bool)):
            raise ConfigValidationError(f"{path}: {key} must be numeric")
    for key in ("minItems", "maxItems", "minLength", "maxLength"):
        if key in schema and (not isinstance(schema[key], int) or schema[key] < 0):
            raise ConfigValidationError(f"{path}: {key} must be a non-negative integer")
    return schema


def validate_payload(payload: Any, schema: dict[str, Any], *, path: str = "$") -> None:
    """Validate a JSON value against the supported schema subset."""
    validate_schema(schema)
    schema_type = schema.get("type")
    if schema_type and not _matches_type(payload, schema_type):
        raise ConfigValidationError(f"{path}: expected {schema_type}")
    if "enum" in schema and payload not in schema["enum"]:
        raise ConfigValidationError(f"{path}: value is not one of the allowed enum values")
    if schema.get("format") == "secret-reference" and (
        not isinstance(payload, str) or not payload.startswith("secret://")
    ):
        raise ConfigValidationError(f"{path}: must be a secret:// reference")
    if isinstance(payload, dict):
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        missing = [key for key in required if key not in payload]
        if missing:
            raise ConfigValidationError(f"{path}: missing required properties: {', '.join(missing)}")
        if not schema.get("additionalProperties", True):
            unexpected = set(payload) - set(properties)
            if unexpected:
                raise ConfigValidationError(
                    f"{path}: unexpected properties: {', '.join(sorted(unexpected))}"
                )
        for key, child in properties.items():
            if key in payload:
                validate_payload(payload[key], child, path=f"{path}.{key}")
    if isinstance(payload, list):
        _validate_length(payload, schema, path)
        if "items" in schema:
            for index, value in enumerate(payload):
                validate_payload(value, schema["items"], path=f"{path}[{index}]")
    if isinstance(payload, str):
        _validate_length(payload, schema, path)
    if _is_number(payload):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if minimum is not None and payload < minimum:
            raise ConfigValidationError(f"{path}: must be >= {minimum}")
        if maximum is not None and payload > maximum:
            raise ConfigValidationError(f"{path}: must be <= {maximum}")


def validate_rollout(rollout: Any, schema: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate ordered basis-point rollout rules and their alternate payloads."""
    if not isinstance(rollout, list):
        raise ConfigValidationError("rollout must be an array")
    normalized: list[dict[str, Any]] = []
    for index, rule in enumerate(rollout):
        if not isinstance(rule, dict):
            raise ConfigValidationError(f"rollout[{index}] must be an object")
        if set(rule) - {"basisPoints", "name", "payload"}:
            raise ConfigValidationError(f"rollout[{index}] contains unsupported fields")
        basis_points = rule.get("basisPoints")
        if not isinstance(basis_points, int) or isinstance(basis_points, bool):
            raise ConfigValidationError(f"rollout[{index}].basisPoints must be an integer")
        if not 0 <= basis_points <= 10_000:
            raise ConfigValidationError(f"rollout[{index}].basisPoints must be between 0 and 10000")
        if "payload" not in rule:
            raise ConfigValidationError(f"rollout[{index}].payload is required")
        validate_payload(rule["payload"], schema, path=f"rollout[{index}].payload")
        name = rule.get("name")
        if name is not None and (not isinstance(name, str) or not name.strip()):
            raise ConfigValidationError(f"rollout[{index}].name must be a non-empty string")
        normalized.append(
            {"basisPoints": basis_points, "payload": rule["payload"], **({"name": name} if name else {})}
        )
    return normalized


def iter_secret_references(value: Any) -> Iterable[str]:
    """Yield secret names from nested JSON values without resolving them."""
    if isinstance(value, str) and value.startswith("secret://"):
        name = value.removeprefix("secret://")
        if name:
            yield name
    elif isinstance(value, dict):
        for child in value.values():
            yield from iter_secret_references(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_secret_references(child)


def _matches_type(value: Any, schema_type: str) -> bool:
    return {
        "array": isinstance(value, list),
        "boolean": isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "null": value is None,
        "number": _is_number(value),
        "object": isinstance(value, dict),
        "string": isinstance(value, str),
    }[schema_type]


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _validate_length(value: Any, schema: dict[str, Any], path: str) -> None:
    min_key, max_key = ("minItems", "maxItems") if isinstance(value, list) else ("minLength", "maxLength")
    if min_key in schema and len(value) < schema[min_key]:
        raise ConfigValidationError(f"{path}: must contain at least {schema[min_key]} items")
    if max_key in schema and len(value) > schema[max_key]:
        raise ConfigValidationError(f"{path}: must contain at most {schema[max_key]} items")

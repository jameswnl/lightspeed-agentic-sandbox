"""Small synthetic JSON Schema dicts for structured-output contract tests."""

from __future__ import annotations

from typing import Any

FLAT_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "success": {"type": "boolean"},
        "summary": {"type": "string"},
        "ticketId": {"type": "string"},
    },
    "required": ["success", "summary", "ticketId"],
}

NESTED_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "success": {"type": "boolean"},
        "summary": {"type": "string"},
        "items": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "count": {"type": "integer"},
                },
                "required": ["name", "count"],
            },
        },
    },
    "required": ["success", "summary", "items"],
}

ECHO_TOKEN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "success": {"type": "boolean"},
        "summary": {"type": "string"},
        "token": {"type": "string", "minLength": 16, "pattern": "^[0-9a-f]+$"},
        "status": {"type": "string", "enum": ["ok"]},
    },
    "required": ["success", "summary", "token", "status"],
}

CONTEXT_NAMESPACES_ECHO_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "success": {"type": "boolean"},
        "summary": {"type": "string"},
        "namespaces": {"type": "string", "minLength": 1},
    },
    "required": ["success", "summary", "namespaces"],
}

CONTEXT_PREVIOUS_ATTEMPTS_ECHO_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "success": {"type": "boolean"},
        "summary": {"type": "string"},
        "firstFailureReason": {"type": "string", "minLength": 1},
    },
    "required": ["success", "summary", "firstFailureReason"],
}

CONTEXT_APPROVED_OPTION_ECHO_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "success": {"type": "boolean"},
        "summary": {"type": "string"},
        "approvedTitle": {"type": "string", "minLength": 1},
        "rootCause": {"type": "string", "minLength": 1},
    },
    "required": ["success", "summary", "approvedTitle", "rootCause"],
}

# Strict schema used with a prompt that encourages invalid / non-JSON output.
STRICT_CONFLICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "success": {"type": "boolean"},
        "summary": {"type": "string"},
        "onlyFieldAlpha": {"type": "string", "const": "alpha"},
        "onlyFieldBeta": {"type": "integer", "const": 42},
    },
    "required": ["success", "summary", "onlyFieldAlpha", "onlyFieldBeta"],
}

"""MCP header utilities for secret-backed auth headers."""

from __future__ import annotations

import os


def resolve_headers(headers: dict[str, str] | None) -> dict[str, str]:
    """Resolve MCP server headers, reading file-reference values from disk.

    Headers with values starting with 'file:' are replaced with the
    contents of the referenced file. This supports the Cloud Agents
    secret mount pattern where auth tokens are injected as files.

    Args:
        headers: Raw header dict from LIGHTSPEED_MCP_SERVERS config.

    Returns:
        Resolved header dict with file references replaced by contents.
    """
    if not headers:
        return {}

    resolved = {}
    for key, value in headers.items():
        if value.startswith("file:"):
            path = value[5:]
            try:
                resolved[key] = open(path).read().strip()
            except OSError:
                resolved[key] = value
        else:
            resolved[key] = value
    return resolved

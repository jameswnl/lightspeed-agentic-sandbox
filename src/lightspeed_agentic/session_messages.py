"""Consumer for CLI session messages written to a JSONL file.

The server writes messages to a JSONL file via ``spawner.write_file()``.
This module provides a consumer that watches for new lines and returns
messages added since the last check.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_MESSAGES_PATH = Path("/var/run/cli-session/messages.jsonl")


class SessionMessageConsumer:
    """Watch a JSONL file for new messages appended by the CLI session server.

    Parameters:
        path: Explicit file path. When *None*, falls back to the
            ``CLI_SESSION_MESSAGES_PATH`` env var, then to the built-in
            default ``/var/run/cli-session/messages.jsonl``.
    """

    def __init__(self, path: Path | None = None) -> None:
        if path is not None:
            self.path = path
        else:
            env = os.environ.get("CLI_SESSION_MESSAGES_PATH")
            self.path = Path(env) if env else DEFAULT_MESSAGES_PATH

        self._offset: int = 0

    def get_new_messages(self) -> list[dict[str, Any]]:
        """Return messages appended since the last call.

        Reads from the stored byte offset, parses each JSONL line, skips
        blank or malformed lines, and advances the offset so the next call
        only returns new content.
        """
        if not self.path.exists():
            return []

        messages: list[dict[str, Any]] = []
        with self.path.open("r") as f:
            f.seek(self._offset)
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    messages.append(json.loads(stripped))
                except json.JSONDecodeError:
                    logger.warning("Skipping malformed JSONL line: %s", stripped)
            self._offset = f.tell()

        return messages

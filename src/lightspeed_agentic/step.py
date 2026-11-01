"""Step CLI — run one agent step over stdin/stdout instead of HTTP.

Usage: python3 -P -m lightspeed_agentic.step [--grace-seconds N] < request.json

Contract (see .ai/spec/what/step-cli.md):
- stdin: one StepRequest JSON object ended by a newline or EOF, at most 1 MiB.
- stdout: JSONL protocol only — {"v":1,"type":"event","event":{ts,type,data}} lines,
  then exactly one {"v":1,"type":"result","status_code":...,"body":{...}}.
  Everything else written to fd 1 (prints, child processes) lands on stderr.
- runDir/result.json: the result record, written atomically before the final line.
- Exit codes: 0 result written, 2 request rejected, 3 internal error, 124 hard deadline.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import math
import os
import signal
import stat
import sys
from types import FrameType
from typing import Any

from pydantic import ValidationError

from lightspeed_agentic.config import resolve_sdk
from lightspeed_agentic.factory import create_provider
from lightspeed_agentic.routes import _resolve_router_model
from lightspeed_agentic.routes.models import StepRequest
from lightspeed_agentic.run import execute_run
from lightspeed_agentic.sdk_patches import apply_sdk_patches
from lightspeed_agentic.tracing import init_tracer, shutdown_tracer
from lightspeed_agentic.types import AgentProvider

logger = logging.getLogger("lightspeed_agentic")

MAX_STDIN_BYTES = 1024 * 1024  # includes the terminating newline
RUN_ROOT = "/tmp/lsrun"  # noqa: S108
DEFAULT_TIMEOUT_MS = 300_000
MAX_TURNS = 200

EXIT_OK = 0
EXIT_REJECTED = 2
EXIT_INTERNAL = 3
EXIT_DEADLINE = 124


def _check_run_dir(path: str) -> str | None:
    """Return an error message unless path is an existing 0700 child of RUN_ROOT we own."""
    if os.path.dirname(path) != RUN_ROOT or os.path.basename(path) in ("", ".", ".."):
        return f"runDir must be a direct child of {RUN_ROOT}"
    try:
        st = os.lstat(path)
    except OSError:
        return "runDir does not exist"
    if (
        not stat.S_ISDIR(st.st_mode)
        or stat.S_IMODE(st.st_mode) != 0o700
        or st.st_uid != os.getuid()
    ):
        return "runDir must be a mode 0700 directory owned by the current user"
    return None


def _write_new(path: str, content: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())


def _write_result(run_dir: str, record: dict[str, Any]) -> None:
    path = os.path.join(run_dir, "result.json")
    if os.path.lexists(path):
        raise FileExistsError(f"{path} already exists")
    tmp = os.path.join(run_dir, f".result.json.{os.getpid()}")
    _write_new(tmp, json.dumps(record))
    os.rename(tmp, path)


def _hard_deadline(_signum: int, _frame: FrameType | None) -> None:
    logger.error("[step] hard deadline exceeded, exiting")
    os._exit(EXIT_DEADLINE)


def main(argv: list[str] | None = None, *, provider: AgentProvider | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -P -m lightspeed_agentic.step")
    parser.add_argument(
        "--grace-seconds",
        type=int,
        default=30,
        help="seconds past timeout_ms before the hard deadline exits with 124",
    )
    args = parser.parse_args(argv)

    with contextlib.suppress(PermissionError):  # already a process group leader
        os.setsid()

    # Keep a private handle on the real stdout and point fd 1 at stderr, so only
    # protocol lines ever reach stdout.
    proto = os.fdopen(os.dup(1), "w", encoding="utf-8")
    os.dup2(2, 1)
    logging.basicConfig(
        level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s"
    )

    def emit(record: dict[str, Any]) -> None:
        proto.write(json.dumps(record) + "\n")
        proto.flush()

    def reject(status_code: int, body: dict[str, Any]) -> int:
        logger.warning("[step] request rejected (%d): %s", status_code, body["error"])
        emit({"v": 1, "type": "result", "status_code": status_code, "body": body})
        return EXIT_REJECTED

    raw = sys.stdin.buffer.readline(MAX_STDIN_BYTES + 1)
    if len(raw) > MAX_STDIN_BYTES:
        return reject(413, {"error": f"Request exceeds {MAX_STDIN_BYTES} bytes"})
    try:
        req = StepRequest.model_validate_json(raw)
    except ValidationError as e:
        detail = json.loads(e.json(include_url=False, include_input=False))
        return reject(422, {"error": "Invalid request", "detail": detail})
    if (error := _check_run_dir(req.runDir)) is not None:
        return reject(422, {"error": error})

    try:
        timeout_ms = req.timeout_ms if req.timeout_ms is not None else DEFAULT_TIMEOUT_MS
        signal.signal(signal.SIGALRM, _hard_deadline)
        signal.alarm(max(1, math.ceil(timeout_ms / 1000) + args.grace_seconds))
        _write_new(os.path.join(req.runDir, "pid"), str(os.getpid()))

        apply_sdk_patches()
        if provider is None:
            provider = create_provider(resolve_sdk().name)
        init_tracer()
        try:
            outcome = asyncio.run(
                execute_run(
                    req,
                    provider=provider,
                    model=_resolve_router_model(provider.name),
                    skills_dir=os.environ.get("LIGHTSPEED_SKILLS_DIR", "/app/skills"),
                    max_turns=MAX_TURNS,
                    default_timeout_ms=DEFAULT_TIMEOUT_MS,
                    audit_enabled=os.environ.get("LIGHTSPEED_AUDIT_ENABLED", "").strip().lower()
                    == "true",
                    traceparent=req.traceparent,
                    event_sink=lambda event: emit({"v": 1, "type": "event", "event": event}),
                )
            )
        finally:
            shutdown_tracer()

        record = {
            "v": 1,
            "type": "result",
            "status_code": outcome.status_code,
            "body": outcome.body,
        }
        _write_result(req.runDir, record)
    except Exception:
        logger.exception("[step] internal error")
        return EXIT_INTERNAL

    emit(record)
    signal.alarm(0)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())

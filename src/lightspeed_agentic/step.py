"""Step CLI — run one agent step over stdin/stdout instead of HTTP.

Usage: python3 -P -m lightspeed_agentic.step [--grace-seconds N] < request.json

Contract (see .ai/spec/what/step-cli.md):
- stdin: one StepRequest JSON object ended by a newline or EOF, at most 1 MiB.
- stdout: JSONL protocol only — {"v":1,"type":"event","event":{ts,type,data}} lines,
  then exactly one {"v":1,"type":"result","status_code":...,"body":{...}}.
  Everything else written to fd 1 (prints, child processes) lands on stderr.
- runDir/result.json: the result record, written atomically before the final line.
- Exit codes: 0 result written, 2 request rejected, 3 internal error, 4 result file
  written but stdout closed, 124 hard deadline.
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
from lightspeed_agentic.routes import resolve_router_model
from lightspeed_agentic.routes.models import StepRequest
from lightspeed_agentic.run import execute_run
from lightspeed_agentic.sdk_patches import apply_sdk_patches
from lightspeed_agentic.tracing import init_tracer, shutdown_tracer
from lightspeed_agentic.types import DEFAULT_MAX_TURNS, DEFAULT_TIMEOUT_MS, AgentProvider

logger = logging.getLogger("lightspeed_agentic")

MAX_STDIN_BYTES = 1024 * 1024  # includes the terminating newline
RUN_ROOT = "/tmp/lsrun"  # noqa: S108

EXIT_OK = 0
EXIT_REJECTED = 2
EXIT_INTERNAL = 3
EXIT_NO_STDOUT = 4
EXIT_DEADLINE = 124


def _open_run_dir(path: str) -> int:
    """Open runDir once and return its fd; raise ValueError unless it is safe to use.

    RUN_ROOT must be a real directory owned by us or root and not writable by others;
    runDir must be a real 0700 directory directly under it, owned by us.
    """
    name = os.path.basename(path)
    if os.path.dirname(path) != RUN_ROOT or name in ("", ".", ".."):
        raise ValueError(f"runDir must be a direct child of {RUN_ROOT}")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        root_fd = os.open(RUN_ROOT, flags)
    except OSError:
        raise ValueError(f"{RUN_ROOT} must be an existing directory") from None
    try:
        st = os.fstat(root_fd)
        if st.st_uid not in (os.getuid(), 0) or st.st_mode & 0o022:
            raise ValueError(f"{RUN_ROOT} must be owned by us or root and not writable by others")
        try:
            dir_fd = os.open(name, flags, dir_fd=root_fd)
        except OSError:
            raise ValueError("runDir must be an existing directory, not a symlink") from None
    finally:
        os.close(root_fd)
    st = os.fstat(dir_fd)
    if stat.S_IMODE(st.st_mode) != 0o700 or st.st_uid != os.getuid():
        os.close(dir_fd)
        raise ValueError("runDir must be a mode 0700 directory owned by the current user")
    return dir_fd


def _write_new(dir_fd: int, name: str, content: str) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    fd = os.open(name, flags, 0o600, dir_fd=dir_fd)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())


def _write_result(dir_fd: int, content: str) -> None:
    """Publish content as result.json; never replaces an existing entry."""
    tmp = f".result.json.{os.getpid()}"
    try:
        _write_new(dir_fd, tmp, content)
        os.link(tmp, "result.json", src_dir_fd=dir_fd, dst_dir_fd=dir_fd, follow_symlinks=False)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp, dir_fd=dir_fd)


def _harden_process() -> None:
    """Point stdin at /dev/null and, on Linux, block same-uid ptrace and /proc/<pid>/fd access."""
    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull, 0)
    os.close(devnull)
    if sys.platform == "linux":
        import ctypes

        with contextlib.suppress(OSError, AttributeError):
            ctypes.CDLL(None).prctl(4, 0, 0, 0, 0)  # 4 = PR_SET_DUMPABLE


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

    # Keep a private fd for the real stdout and point fd 1 at stderr, so only
    # protocol lines ever reach stdout.
    proto_fd = os.dup(1)
    os.dup2(2, 1)
    logging.basicConfig(
        level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s"
    )
    stdout_open = True

    def emit(line: str) -> bool:
        """Write one protocol line; after the first write error, stop writing."""
        nonlocal stdout_open
        if stdout_open:
            data = (line + "\n").encode()
            try:
                while data:
                    data = data[os.write(proto_fd, data) :]
            except OSError:
                logger.warning("[step] stdout closed, no more protocol lines", exc_info=True)
                stdout_open = False
        return stdout_open

    def emit_event(event: dict[str, Any]) -> None:
        try:
            line = json.dumps({"v": 1, "type": "event", "event": event}, allow_nan=False)
        except ValueError:
            logger.warning("[step] dropping event that is not strict JSON: %s", event["type"])
            return
        emit(line)

    def reject(status_code: int, body: dict[str, Any]) -> int:
        logger.warning("[step] request rejected (%d): %s", status_code, body["error"])
        emit(json.dumps({"v": 1, "type": "result", "status_code": status_code, "body": body}))
        return EXIT_REJECTED

    raw = sys.stdin.buffer.readline(MAX_STDIN_BYTES + 1)
    _harden_process()
    if len(raw) > MAX_STDIN_BYTES:
        return reject(413, {"error": f"Request exceeds {MAX_STDIN_BYTES} bytes"})
    try:
        req = StepRequest.model_validate_json(raw)
    except ValidationError as e:
        detail = json.loads(e.json(include_url=False, include_input=False))
        return reject(422, {"error": "Invalid request", "detail": detail})
    try:
        dir_fd = _open_run_dir(req.runDir)
    except ValueError as e:
        return reject(422, {"error": str(e)})

    try:
        timeout_ms = req.timeout_ms if req.timeout_ms is not None else DEFAULT_TIMEOUT_MS
        signal.signal(signal.SIGALRM, _hard_deadline)
        signal.alarm(max(1, math.ceil(timeout_ms / 1000) + args.grace_seconds))
        _write_new(dir_fd, "pid", str(os.getpid()))

        apply_sdk_patches()
        if provider is None:
            provider = create_provider(resolve_sdk().name)
        init_tracer()
        try:
            outcome = asyncio.run(
                execute_run(
                    req,
                    provider=provider,
                    model=resolve_router_model(provider.name),
                    skills_dir=os.environ.get("LIGHTSPEED_SKILLS_DIR", "/app/skills"),
                    max_turns=DEFAULT_MAX_TURNS,
                    default_timeout_ms=DEFAULT_TIMEOUT_MS,
                    audit_enabled=os.environ.get("LIGHTSPEED_AUDIT_ENABLED", "").strip().lower()
                    == "true",
                    traceparent=req.traceparent,
                    event_sink=emit_event,
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
        line = json.dumps(record, allow_nan=False)
        # Past this point the result is published in full or not at all: the hard
        # deadline can no longer fire between the result file and the result line.
        signal.alarm(0)
        _write_result(dir_fd, line)
    except Exception:
        logger.exception("[step] internal error")
        return EXIT_INTERNAL

    return EXIT_OK if emit(line) else EXIT_NO_STDOUT


if __name__ == "__main__":
    sys.exit(main())

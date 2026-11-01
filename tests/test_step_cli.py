"""Process-boundary tests for ``python -P -m lightspeed_agentic.step``.

Every test spawns the CLI as a real subprocess. Provider-dependent cases go
through ``tests.step_fakes``, which runs the real ``step.main`` with a fake
provider; rejection cases run the shipped module directly.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from lightspeed_agentic.routes import build_router

from .step_fakes import make_provider

REPO_ROOT = Path(__file__).resolve().parent.parent
RUN_ROOT = Path("/tmp/lsrun")  # noqa: S108
MAX_STDIN_BYTES = 1024 * 1024


@pytest.fixture
def run_dir():
    RUN_ROOT.mkdir(mode=0o700, exist_ok=True)
    path = tempfile.mkdtemp(dir=RUN_ROOT)  # created with mode 0700
    yield path
    shutil.rmtree(path, ignore_errors=True)


def _env(**extra: str) -> dict[str, str]:
    env = dict(os.environ)
    for key in ("AGENT_EVENT_LOG", "LIGHTSPEED_AUDIT_ENABLED", "LIGHTSPEED_MCP_SERVERS"):
        env.pop(key, None)
    env["PYTHONPATH"] = str(REPO_ROOT)
    env.update(extra)
    return env


def _cmd(scenario: str | None, *args: str) -> list[str]:
    if scenario is None:
        return [sys.executable, "-P", "-m", "lightspeed_agentic.step", *args]
    return [sys.executable, "-P", "-m", "tests.step_fakes", scenario, *args]


def _run(
    stdin: bytes, scenario: str | None = "ok", *args: str, **env: str
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(  # noqa: S603
        _cmd(scenario, *args),
        input=stdin,
        capture_output=True,
        cwd="/",
        env=_env(**env),
        timeout=60,
        check=False,
    )


def _request(run_dir: str, **fields: Any) -> dict[str, Any]:
    return {"query": "Diagnose the issue", "stepId": "step-1", "runDir": run_dir, **fields}


def _line(req: dict[str, Any]) -> bytes:
    return (json.dumps(req) + "\n").encode()


def _records(stdout: bytes) -> list[dict[str, Any]]:
    """Every stdout line must be a v1 protocol record."""
    records = [json.loads(line) for line in stdout.decode().splitlines()]
    assert all(r["v"] == 1 and r["type"] in ("event", "result") for r in records)
    return records


def _result(records: list[dict[str, Any]]) -> dict[str, Any]:
    results = [r for r in records if r["type"] == "result"]
    assert len(results) == 1
    assert records[-1] is results[0]
    return results[0]


def test_happy_path_contract(run_dir):
    proc = _run(_line(_request(run_dir)))

    assert proc.returncode == 0, proc.stderr.decode()
    records = _records(proc.stdout)
    events = [r["event"] for r in records if r["type"] == "event"]
    assert [e["type"] for e in events] == ["tool_call", "tool_result", "result"]
    assert events[0]["data"] == {"name": "Bash", "input": "oc get pods"}
    assert all(set(e) == {"ts", "type", "data"} for e in events)

    result = _result(records)
    assert result["status_code"] == 200
    assert result["body"] == {"success": True, "summary": "mock result", "rootCause": "oom"}

    assert json.loads(Path(run_dir, "result.json").read_text()) == result
    assert int(Path(run_dir, "pid").read_text()) > 0
    assert "[agent] Starting query" in proc.stderr.decode()


def test_openai_agents_usage_workaround_applied(run_dir):
    proc = _run(_line(_request(run_dir)), "openai_usage")

    assert proc.returncode == 0, proc.stderr.decode()
    assert _result(_records(proc.stdout))["body"] == {"success": True, "summary": "mock result"}


def test_stdout_clean_under_noise(run_dir):
    proc = _run(_line(_request(run_dir)), "noisy", LIGHTSPEED_AUDIT_ENABLED="true")

    assert proc.returncode == 0, proc.stderr.decode()
    _result(_records(proc.stdout))
    stderr = proc.stderr.decode()
    for junk in ("print junk", "child junk", "verbose junk", "audit.agent"):
        assert junk in stderr


def test_traceparent_from_stdin(run_dir):
    trace_id = "4bf92f3577b34da6a3ce929d0e0e4736"
    req = _request(run_dir, traceparent=f"00-{trace_id}-00f067aa0ba902b7-01")

    proc = _run(_line(req), "ok", LIGHTSPEED_AUDIT_ENABLED="true")

    assert proc.returncode == 0, proc.stderr.decode()
    assert f'"trace_id": "{trace_id}"' in proc.stderr.decode()


def test_newline_terminates_request_without_eof(run_dir):
    proc = subprocess.Popen(  # noqa: S603
        _cmd("ok"),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd="/",
        env=_env(),
    )
    try:
        assert proc.stdin is not None
        assert proc.stdout is not None
        proc.stdin.write(_line(_request(run_dir)))
        proc.stdin.flush()
        assert proc.wait(timeout=30) == 0
        assert _result(_records(proc.stdout.read()))["status_code"] == 200
    finally:
        proc.kill()
        proc.communicate()


def test_oversize_stdin_rejected_before_provider(run_dir):
    proc = _run(b"x" * (MAX_STDIN_BYTES + 1))

    assert proc.returncode == 2
    records = _records(proc.stdout)
    assert [r["type"] for r in records] == ["result"]
    assert records[0]["status_code"] == 413
    assert not Path(run_dir, "result.json").exists()


@pytest.mark.parametrize(
    "stdin",
    [
        b"{not json\n",
        b"",
        b"[]\n",
        b'{"stepId": "step-1", "runDir": "/tmp/lsrun/x"}\n',
        b'{"query": "q", "stepId": "Bad_Id", "runDir": "/tmp/lsrun/x"}\n',
    ],
    ids=["malformed", "empty", "not-object", "missing-query", "bad-step-id"],
)
def test_invalid_request_rejected(stdin):
    proc = _run(stdin, None)

    assert proc.returncode == 2, proc.stderr.decode()
    result = _result(_records(proc.stdout))
    assert result["status_code"] == 422


def test_unknown_key_rejected(run_dir):
    proc = _run(_line(_request(run_dir, bogus=1)), None)

    assert proc.returncode == 2
    assert _result(_records(proc.stdout))["status_code"] == 422


def test_run_dir_outside_root_rejected(tmp_path):
    tmp_path.chmod(0o700)
    proc = _run(_line(_request(str(tmp_path))), None)

    assert proc.returncode == 2
    assert _result(_records(proc.stdout))["status_code"] == 422
    assert not (tmp_path / "pid").exists()


def test_run_dir_wrong_mode_rejected(run_dir):
    Path(run_dir).chmod(0o755)
    proc = _run(_line(_request(run_dir)), None)

    assert proc.returncode == 2
    assert _result(_records(proc.stdout))["status_code"] == 422


def test_existing_result_symlink_refused(run_dir, tmp_path):
    target = tmp_path / "target"
    Path(run_dir, "result.json").symlink_to(target)

    proc = _run(_line(_request(run_dir)))

    assert proc.returncode == 3
    assert not [r for r in _records(proc.stdout) if r["type"] == "result"]
    assert not target.exists()


def test_soft_deadline_writes_502_result(run_dir):
    proc = _run(_line(_request(run_dir, timeout_ms=300)), "hang")

    assert proc.returncode == 0, proc.stderr.decode()
    result = _result(_records(proc.stdout))
    assert result["status_code"] == 502
    assert result["body"] == {"error": "Agent timed out after 300ms"}
    assert json.loads(Path(run_dir, "result.json").read_text()) == result


def test_hard_deadline_exits_124(run_dir):
    start = time.monotonic()
    proc = _run(_line(_request(run_dir, timeout_ms=200)), "stubborn", "--grace-seconds", "1")

    assert proc.returncode == 124, proc.stderr.decode()
    assert time.monotonic() - start < 15
    assert not [r for r in _records(proc.stdout) if r["type"] == "result"]
    assert not Path(run_dir, "result.json").exists()


def test_kill_process_group_via_pid_file(run_dir):
    proc = subprocess.Popen(  # noqa: S603
        _cmd("hang"),
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd="/",
        env=_env(),
    )
    try:
        assert proc.stdin is not None
        proc.stdin.write(_line(_request(run_dir)))
        proc.stdin.close()
        pid_file = Path(run_dir, "pid")
        deadline = time.monotonic() + 30
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        pgid = int(pid_file.read_text())
        assert pgid == proc.pid
        os.killpg(pgid, signal.SIGTERM)
        assert proc.wait(timeout=10) == -signal.SIGTERM
        assert not Path(run_dir, "result.json").exists()
    finally:
        proc.kill()
        proc.wait()


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["ok", "text", "empty", "infra", "agent_error"])
async def test_http_run_parity(run_dir, scenario):
    run_req: dict[str, Any] = {
        "query": "Diagnose the issue",
        "systemPrompt": "You are an SRE agent.",
        "context": {"targetNamespaces": ["default"], "attempt": 1},
        "timeout_ms": 10_000,
    }
    app = FastAPI()
    app.include_router(
        build_router(make_provider(scenario), skills_dir="/workspace", model="test-model"),
        prefix="/v1/agent",
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/v1/agent/run", json=run_req)

    proc = _run(_line(_request(run_dir, **run_req)), scenario)

    assert proc.returncode == 0, proc.stderr.decode()
    result = _result(_records(proc.stdout))
    assert (result["status_code"], result["body"]) == (resp.status_code, resp.json())


def test_closed_stdout_keeps_result_file_valid(run_dir):
    read_end, write_end = os.pipe()
    os.close(read_end)
    try:
        proc = subprocess.run(  # noqa: S603
            _cmd("ok"),
            input=_line(_request(run_dir)),
            stdout=write_end,
            stderr=subprocess.PIPE,
            cwd="/",
            env=_env(),
            timeout=60,
            check=False,
        )
    finally:
        os.close(write_end)

    assert proc.returncode == 4, proc.stderr.decode()
    result = json.loads(Path(run_dir, "result.json").read_text())
    assert result["status_code"] == 200
    assert result["body"]["summary"] == "mock result"


def _reject_constant(name: str) -> None:
    raise ValueError(f"non-strict JSON constant {name}")


def test_nan_never_reaches_protocol_output(run_dir):
    proc = _run(_line(_request(run_dir)), "nan")

    assert proc.returncode == 0, proc.stderr.decode()
    for line in proc.stdout.decode().splitlines():
        json.loads(line, parse_constant=_reject_constant)
    records = _records(proc.stdout)
    assert [r["event"]["type"] for r in records if r["type"] == "event"] == ["tool_call"]
    assert _result(records)["body"] == {"success": True, "summary": "s", "x": None}


def test_result_created_mid_run_is_not_overwritten(run_dir):
    proc = _run(_line(_request(run_dir)), "plant_result", STEP_FAKE_RUN_DIR=run_dir)

    assert proc.returncode == 3, proc.stderr.decode()
    assert Path(run_dir, "result.json").read_text() == "planted"
    assert sorted(p.name for p in Path(run_dir).iterdir()) == ["pid", "result.json"]


def test_run_root_writable_by_others_rejected(tmp_path):
    run_root = tmp_path / "lsrun"
    run_dir = run_root / "step"
    run_dir.mkdir(parents=True, mode=0o700)
    run_root.chmod(0o777)

    proc = _run(_line(_request(str(run_dir))), "ok", STEP_FAKE_RUN_ROOT=str(run_root))

    run_root.chmod(0o700)
    assert proc.returncode == 2, proc.stderr.decode()
    assert _result(_records(proc.stdout))["status_code"] == 422
    assert not (run_dir / "pid").exists()


def test_run_dir_symlink_rejected(run_dir):
    link = Path(run_dir + "-link")
    link.symlink_to(run_dir)
    try:
        proc = _run(_line(_request(str(link))), None)
    finally:
        link.unlink()

    assert proc.returncode == 2
    assert _result(_records(proc.stdout))["status_code"] == 422


def test_stdin_detached_and_process_hardened(run_dir):
    proc = _run(_line(_request(run_dir)), "process_state")

    assert proc.returncode == 0, proc.stderr.decode()
    assert _result(_records(proc.stdout))["body"]["success"] is True


def test_timeout_ms_capped(run_dir):
    proc = _run(_line(_request(run_dir, timeout_ms=10**13)), None)

    assert proc.returncode == 2, proc.stderr.decode()
    assert _result(_records(proc.stdout))["status_code"] == 422


def test_unread_stdout_bounds_final_result_line(run_dir):
    proc = subprocess.Popen(  # noqa: S603
        [*_cmd("huge_error"), "--grace-seconds", "1"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        cwd="/",
        env=_env(),
    )
    try:
        assert proc.stdin is not None
        proc.stdin.write(_line(_request(run_dir)))
        proc.stdin.close()
        assert proc.wait(timeout=20) == 4
        result = json.loads(Path(run_dir, "result.json").read_text())
        assert result["body"]["summary"].startswith("Agent error: xxx")
    finally:
        proc.kill()
        proc.wait()


def test_closed_stdin_is_internal_error():
    proc = subprocess.run(  # noqa: S603
        _cmd(None),
        capture_output=True,
        cwd="/",
        env=_env(),
        timeout=60,
        check=False,
        preexec_fn=lambda: os.close(0),
    )

    assert proc.returncode == 3, proc.stderr.decode()
    assert proc.stdout == b""


def test_bad_arguments_exit_2_without_result_line():
    proc = _run(b"", None, "--bogus")

    assert proc.returncode == 2
    assert proc.stdout == b""


def test_prctl_failure_is_logged(monkeypatch, caplog):
    from lightspeed_agentic import step

    class FailingLibc:
        def prctl(self, *_args: int) -> int:
            return -1

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr("ctypes.CDLL", lambda *_args, **_kwargs: FailingLibc())
    with caplog.at_level("WARNING", logger="lightspeed_agentic"):
        step._set_non_dumpable()
    assert "PR_SET_DUMPABLE" in caplog.text


def _full_pipe() -> tuple[int, int]:
    """Return (read, write) ends of a pipe whose buffer is already full."""
    read_end, write_end = os.pipe()
    os.set_blocking(write_end, False)
    for chunk in (65536, 1):
        with contextlib.suppress(BlockingIOError):
            while True:
                os.write(write_end, b"x" * chunk)
    os.set_blocking(write_end, True)
    return read_end, write_end


def _spawn(scenario: str | None, stdin: bytes, *args: str, stdout: int, stderr: int) -> int:
    """Run the CLI with the given stdout/stderr fds; return its exit code.

    Fails if the CLI does not exit by itself within 20 s.
    """
    proc = subprocess.Popen(  # noqa: S603
        _cmd(scenario, *args),
        stdin=subprocess.PIPE,
        stdout=stdout,
        stderr=stderr,
        cwd="/",
        env=_env(),
    )
    try:
        assert proc.stdin is not None
        proc.stdin.write(stdin)
        proc.stdin.close()
        return proc.wait(timeout=20)
    finally:
        proc.kill()
        proc.wait()


def _assert_absent_or_complete(run_dir: str) -> None:
    path = Path(run_dir, "result.json")
    if path.exists():
        assert json.loads(path.read_text())["type"] == "result"


def test_hard_deadline_exits_with_stderr_full(run_dir):
    err_r, err_w = _full_pipe()
    try:
        start = time.monotonic()
        code = _spawn(
            "ok",
            _line(_request(run_dir, timeout_ms=200)),
            "--grace-seconds",
            "1",
            stdout=subprocess.DEVNULL,
            stderr=err_w,
        )
        assert code == 124
        assert time.monotonic() - start < 10
        _assert_absent_or_complete(run_dir)
    finally:
        os.close(err_r)
        os.close(err_w)


def test_final_delivery_deadline_exits_with_stderr_full(run_dir):
    out_r, out_w = _full_pipe()
    err_r, err_w = os.pipe()
    try:
        start = time.monotonic()
        code = _spawn(
            "fill_stderr_later",
            _line(_request(run_dir)),
            "--grace-seconds",
            "2",
            stdout=out_w,
            stderr=err_w,
        )
        assert code == 4
        assert time.monotonic() - start < 10
        assert json.loads(Path(run_dir, "result.json").read_text())["status_code"] == 200
    finally:
        for fd in (out_r, out_w, err_r, err_w):
            os.close(fd)


def test_blocked_publication_interruptible_wait_is_bounded(run_dir):
    """Covers interruptible blocking only (a sleeping fsync); see spec rule 9."""
    start = time.monotonic()
    code = _spawn(
        "slow_publish",
        _line(_request(run_dir)),
        "--grace-seconds",
        "1",
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    assert code == 124
    assert time.monotonic() - start < 10
    _assert_absent_or_complete(run_dir)


def test_rejection_with_huge_unknown_key_is_small_and_bounded():
    out_r, out_w = os.pipe()
    try:
        req = {"query": "q", "stepId": "s", "runDir": "/tmp/lsrun/x", "k" * 900_000: 1}  # noqa: S108
        code = _spawn(
            None,
            _line(req),
            "--grace-seconds",
            "1",
            stdout=out_w,
            stderr=subprocess.DEVNULL,
        )
        assert code == 2
        os.set_blocking(out_r, False)
        line = os.read(out_r, 1 << 20)
        assert len(line) < 4096
        assert json.loads(line)["status_code"] == 422
    finally:
        os.close(out_r)
        os.close(out_w)


def test_rejection_delivery_is_bounded_when_stdout_full():
    out_r, out_w = _full_pipe()
    try:
        start = time.monotonic()
        code = _spawn(
            None, b"{not json\n", "--grace-seconds", "1", stdout=out_w, stderr=subprocess.DEVNULL
        )
        assert code == 2
        assert time.monotonic() - start < 10
    finally:
        os.close(out_r)
        os.close(out_w)


def test_exit_is_bounded_with_buffered_output_and_stuck_thread(run_dir, tmp_path):
    err_r, err_w = os.pipe()
    out_path = tmp_path / "stdout"
    try:
        with out_path.open("wb") as out:
            proc = subprocess.Popen(  # noqa: S603
                [*_cmd("messy_exit"), "--grace-seconds", "1"],
                stdin=subprocess.PIPE,
                stdout=out,
                stderr=err_w,
                cwd="/",
                env=_env(STEP_FAKE_RUN_DIR=run_dir),
            )
            try:
                assert proc.stdin is not None
                proc.stdin.write(_line(_request(run_dir)))
                proc.stdin.close()
                start = time.monotonic()
                assert proc.wait(timeout=20) == 0
                assert time.monotonic() - start < 10
            finally:
                proc.kill()
                proc.wait()
        assert _result(_records(out_path.read_bytes()))["status_code"] == 200
    finally:
        os.close(err_r)
        os.close(err_w)


def test_startup_failure_logging_is_bounded_with_stderr_full():
    err_r, err_w = _full_pipe()
    try:
        proc = subprocess.Popen(  # noqa: S603
            [*_cmd(None), "--grace-seconds", "1"],
            stdout=subprocess.DEVNULL,
            stderr=err_w,
            cwd="/",
            env=_env(),
            preexec_fn=lambda: os.close(0),
        )
        try:
            start = time.monotonic()
            assert proc.wait(timeout=20) == 3
            assert time.monotonic() - start < 10
        finally:
            proc.kill()
            proc.wait()
    finally:
        os.close(err_r)
        os.close(err_w)


def test_base_exception_maps_to_internal_error(run_dir):
    proc = _run(_line(_request(run_dir)), "keyboard_interrupt")

    assert proc.returncode == 3, proc.stderr.decode()
    assert not [r for r in _records(proc.stdout) if r["type"] == "result"]

"""Process-boundary tests for ``python -P -m lightspeed_agentic.step``.

Every test spawns the CLI as a real subprocess. Provider-dependent cases go
through ``tests.step_fakes``, which runs the real ``step.main`` with a fake
provider; rejection cases run the shipped module directly.
"""

from __future__ import annotations

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

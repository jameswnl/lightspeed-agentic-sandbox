# Behavioral spec: step CLI

Cross-references: `run-api.md` (run semantics, response shaping), `configuration.md` (env vars).

## Principles

The step CLI runs exactly one agent step through a process exec instead of HTTP: `python3 -P -m lightspeed_agentic.step`, workdir `/`. It shares `execute_run` (`run.py`) with `POST /v1/agent/run`, so for the same request it produces the same status code and body as `/run` (`run-api.md` rules apply unchanged). `-P` keeps the working directory off `sys.path`.

## Rules

1. **stdin.** Exactly one UTF-8 JSON object, terminated by a newline or EOF. The CLI reads at most 1 MiB (1,048,576 bytes, including the newline) and stops at the first newline, so it never waits for EOF.
2. **StepRequest schema.** `RunRequest` fields plus `stepId` (`^[a-z0-9-]{1,40}$`), `runDir` (string) and optional `traceparent` (W3C header value). `timeout_ms` is capped at 86400000 (24 h). Unknown keys are rejected (`extra="forbid"`). Trace context comes only from `traceparent` on stdin, never from env.
3. **runDir.** Must be a direct child of `/tmp/lsrun/`, already exist, be a real directory (not a symlink), have mode `0700` and be owned by the current uid. `/tmp/lsrun` itself must be a real directory owned by the current uid or root and not group- or world-writable. The caller creates both; the CLI only checks them. The CLI opens runDir once (`O_DIRECTORY|O_NOFOLLOW`) and does all later file operations relative to that fd.
4. **stdout protocol.** JSONL only. At startup the CLI duplicates fd 1 into a private protocol handle and points fd 1 at stderr, so prints, child processes and stdout loggers all land on stderr. Protocol lines:
   - `{"v":1,"type":"event","event":{"ts","type","data"}}`: the same records as `AGENT_EVENT_LOG` (`logging.py`), in order.
   - exactly one final `{"v":1,"type":"result","status_code":<int>,"body":{...}}`. `status_code` and `body` are what `/run` would return.
   - Lines are strict JSON (no `NaN`/`Infinity`). An event that cannot be serialized strictly is dropped with a warning on stderr. A result record that cannot be serialized strictly is an internal error (exit 3). Result bodies are already JSON-mode dumps, so non-finite values arrive there as `null`, the same as `/run`.
   - If writing to stdout fails (for example the reader closed it), the CLI logs once, stops writing protocol lines, and finishes the run so `result.json` stays valid (exit 4).
5. **Logging.** All logging goes to stderr.
6. **Result file.** Before emitting the final result line, the CLI writes the same result record to `runDir/result.json`. It writes a temp file with `O_CREAT|O_EXCL|O_NOFOLLOW`, calls `fsync`, then hard-links it to `result.json` and removes the temp file. The link fails if `result.json` already exists (including as a symlink, or if it was created mid-run); the CLI then exits 3 without replacing it. The temp file is always removed.
7. **pid file.** After validation the CLI writes its PID to `runDir/pid`. It calls `setsid()` at startup, so the PID is also its process group ID: `kill -TERM -<pid>` reaches the CLI and every child.
8. **Soft deadline.** `timeout_ms` (default 300000) is enforced by `execute_run` exactly as on `/run`. A timeout gives a result with status 502 and exit 0.
9. **Hard deadline.** `SIGALRM` fires at `ceil(timeout_ms/1000) + grace` seconds (`--grace-seconds`, default 30). It exits with 124 and writes no result. It bounds providers that ignore cancellation. The alarm is cancelled before the result is published, so the result file and the result line are both present or both absent relative to the deadline. `os._exit` does not signal children.
10. **Rejections.** Exit 2, with a single result line and no result file:
    - stdin over 1 MiB → 413 `{"error"}`
    - invalid JSON or schema → 422 `{"error","detail"}`. `detail` holds the pydantic errors without input values.
    - invalid `runDir` → 422 `{"error"}`
    Rejections happen before the provider is constructed.
11. **Exit codes.**

    | Code | Meaning |
    |---|---|
    | 0 | Result written, any `status_code` |
    | 2 | Request rejected |
    | 3 | Internal error before a result. Provider or config construction, pid/result file write, non-strict result JSON. No result line. |
    | 4 | `result.json` written, but stdout was closed so the result line was not delivered. |
    | 124 | Hard deadline |
    | 137 / 143 (signal) | Killed |

12. **Secrets.** The CLI reads no secrets from argv or stdin. Provider configuration and credentials come from the same env as `/run`.
13. **Process hardening.** After reading the request the CLI points fd 0 at `/dev/null`, so children never inherit the request pipe. On Linux it calls `prctl(PR_SET_DUMPABLE, 0)`, which blocks same-uid ptrace and `/proc/<pid>/fd` access to the CLI process itself. `execve` resets this for child processes.

## Trust model (runner obligations)

The agent's tools run as the same uid as the CLI. They can rewrite `runDir/pid`, pre-create or replace files in `runDir`, write to inherited or reachable fds, and call `setsid` to leave the process group. Therefore the runner:

- MUST treat every stdout line and `result.json` as untrusted, agent-influenced data: parse it strictly, bound its size, and validate it.
- MUST take the step's process identity from its own exec handle, never from `runDir/pid`. The pid file is a convenience for same-sandbox kill commands only.
- MUST tear down the exec or the sandbox itself to stop a step, rather than rely on `kill -<pgid>` or the hard deadline. Children can `setsid` out of the group and survive exit 124.

## Planned

- `[PLANNED]` `--capabilities` subcommand.
- `[PLANNED]` `toolPolicy` on stdin (restricted mode). It is rejected today as an unknown key.
- `[PLANNED]` optional non-secret `config` block on stdin, if exec does not inherit the sandbox env.

## Verification

| Test | Rules |
|---|---|
| [test_step_cli.py](../../../tests/test_step_cli.py) | 1–11, 13. Real subprocesses with `tests/step_fakes.py` fake providers, plus HTTP `/run` parity |
| [test_event_file.py](../../../tests/test_event_file.py) (`TestEventSink`) | 4 (event records) |

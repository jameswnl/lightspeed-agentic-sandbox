# Behavioral spec: step CLI

Cross-references: `run-api.md` (run semantics, response shaping), `configuration.md` (env vars).

## Principles

The step CLI runs exactly one agent step through a process exec instead of HTTP: `python3 -P -m lightspeed_agentic.step`, workdir `/`. It shares `execute_run` (`run.py`) with `POST /v1/agent/run`, so for the same request it produces the same status code and body as `/run` (`run-api.md` rules apply unchanged). `-P` keeps the working directory off `sys.path`.

## Rules

1. **stdin.** Exactly one UTF-8 JSON object, terminated by a newline or EOF. The CLI reads at most 1 MiB (1,048,576 bytes, including the newline) and stops at the first newline, so it never waits for EOF.
2. **StepRequest schema.** `RunRequest` fields plus `stepId` (`^[a-z0-9-]{1,40}$`), `runDir` (string) and optional `traceparent` (W3C header value). Unknown keys are rejected (`extra="forbid"`). Trace context comes only from `traceparent` on stdin, never from env.
3. **runDir.** Must be a direct child of `/tmp/lsrun/`, already exist, be a real directory (not a symlink), have mode `0700` and be owned by the current uid. The caller creates it; the CLI only checks it.
4. **stdout protocol.** JSONL only. At startup the CLI duplicates fd 1 into a private protocol handle and points fd 1 at stderr, so prints, child processes and stdout loggers all land on stderr. Protocol lines:
   - `{"v":1,"type":"event","event":{"ts","type","data"}}`: the same records as `AGENT_EVENT_LOG` (`logging.py`), in order.
   - exactly one final `{"v":1,"type":"result","status_code":<int>,"body":{...}}`. `status_code` and `body` are what `/run` would return.
5. **Logging.** All logging goes to stderr.
6. **Result file.** Before emitting the final result line, the CLI writes the same result record to `runDir/result.json`. It writes a temp file with `O_CREAT|O_EXCL|O_NOFOLLOW`, calls `fsync`, then renames it. If `result.json` already exists (including as a symlink), the CLI refuses and exits 3.
7. **pid file.** After validation the CLI writes its PID to `runDir/pid`. It calls `setsid()` at startup, so the PID is also its process group ID: `kill -TERM -<pid>` reaches the CLI and every child.
8. **Soft deadline.** `timeout_ms` (default 300000) is enforced by `execute_run` exactly as on `/run`. A timeout gives a result with status 502 and exit 0.
9. **Hard deadline.** `SIGALRM` fires at `ceil(timeout_ms/1000) + grace` seconds (`--grace-seconds`, default 30). It exits with 124 and writes no result. It bounds providers that ignore cancellation.
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
    | 3 | Internal error before a result. Provider or config construction, pid/result file write. No result line. |
    | 124 | Hard deadline |
    | 137 / 143 (signal) | Killed |

12. **Secrets.** The CLI reads no secrets from argv or stdin. Provider configuration and credentials come from the same env as `/run`.

## Planned

- `[PLANNED]` `--capabilities` subcommand.
- `[PLANNED]` `toolPolicy` on stdin (restricted mode). It is rejected today as an unknown key.
- `[PLANNED]` optional non-secret `config` block on stdin, if exec does not inherit the sandbox env.

## Verification

| Test | Rules |
|---|---|
| [test_step_cli.py](../../../tests/test_step_cli.py) | 1–11. Real subprocesses with `tests/step_fakes.py` fake providers, plus HTTP `/run` parity |
| [test_event_file.py](../../../tests/test_event_file.py) (`TestEventSink`) | 4 (event records) |

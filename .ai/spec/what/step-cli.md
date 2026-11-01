# Behavioral spec: step CLI

Cross-references: `run-api.md` (run semantics, response shaping), `configuration.md` (env vars).

## Principles

The step CLI runs exactly one agent step through a process exec instead of HTTP: `python3 -P -m lightspeed_agentic.step`, workdir `/`. It shares `execute_run` (`run.py`) with `POST /v1/agent/run`, so for the same request it produces the same status code and body as `/run` (`run-api.md` rules apply unchanged). `-P` keeps the working directory off `sys.path`.

## Rules

1. **stdin.** Exactly one UTF-8 JSON object, terminated by a newline or EOF. The CLI reads at most 1 MiB (1,048,576 bytes, including the newline) and stops at the first newline, so it never waits for EOF. No CLI timer runs while startup input is read (the first alarm is armed right after the request line is read, rule 9): bounding a caller that never sends a line is the exec caller's job (exec timeout).
2. **StepRequest schema.** `RunRequest` fields plus `stepId` (`^[a-z0-9-]{1,40}$`), `runDir` (string) and optional `traceparent` (W3C header value). `timeout_ms` is capped at 86400000 (24 h). Unknown keys are rejected (`extra="forbid"`). Trace context comes only from `traceparent` on stdin, never from env.
3. **runDir.** Must be a direct child of `/tmp/lsrun/`, already exist, be a real directory (not a symlink), have mode `0700` and be owned by the current uid. `/tmp/lsrun` itself must be a real directory owned by the current uid or root and not group- or world-writable. The caller creates both; the CLI only checks them. The CLI opens runDir once (`O_DIRECTORY|O_NOFOLLOW`) and does all later file operations relative to that fd.
4. **stdout protocol.** JSONL only. At startup the CLI duplicates fd 1 into a private protocol handle and points fd 1 at stderr, so prints, child processes and stdout loggers all land on stderr. Protocol lines:
   - `{"v":1,"type":"event","event":{"ts","type","data"}}`: the same records as `AGENT_EVENT_LOG` (`logging.py`), in order.
   - at most one final `{"v":1,"type":"result","status_code":<int>,"body":{...}}`, always the last line. `status_code` and `body` are what `/run` would return. Exactly one complete result line is promised only on exit 0. On exits 2 and 4 it may be missing or truncated (rules 9 and 10), and other exits emit none.
   - Lines are strict JSON (no `NaN`/`Infinity`). An event that cannot be serialized strictly is dropped with a warning on stderr. A result record that cannot be serialized strictly is an internal error (exit 3). Result bodies are already JSON-mode dumps, so non-finite values arrive there as `null`, the same as `/run`.
   - If writing to stdout fails (for example the reader closed it), the CLI logs once, stops writing protocol lines, and finishes the run so `result.json` can still be published (exit 4 once published; rule 9).
   - Publication of `result.json` and delivery of the final result line run under one finalization alarm (rule 9). If the reader does not drain stdout in time, the CLI exits 4. The line may be missing or truncated.
5. **Logging.** All logging goes to stderr.
6. **Result file.** Before emitting the final result line, the CLI writes the same result record to `runDir/result.json`. It writes a temp file with `O_CREAT|O_EXCL|O_NOFOLLOW`, calls `fsync`, then hard-links it to `result.json` and removes the temp file. The link fails if `result.json` already exists (including as a symlink, or if it was created mid-run); the CLI then exits 3 without replacing it. The temp file is removed on normal completion and on handled errors. A deadline (`os._exit`) or abnormal termination may leave a `.result.json.<pid>` temp file behind (rule 9). `result.json` is never partial: it appears only by linking a complete, fsynced temp file.
7. **pid file.** After validation the CLI writes its PID to `runDir/pid`. It calls `setsid()` at startup, so the PID is also its process group ID: `kill -TERM -<pid>` reaches the CLI and every child still in that group. Children can `setsid` out of it (see the trust model).
8. **Soft deadline.** `timeout_ms` (default 300000) is enforced by `execute_run` exactly as on `/run`. A timeout gives a result with status 502 and exit 0.
9. **Deadlines.** From the moment the request line is read until the process exits, some `SIGALRM` is always armed, and every stage that can block is covered: startup hardening and its warnings, rejection delivery, the run, publication, result-line delivery, and process exit. Deadline handlers only call `os._exit`; they do not log, take locks or touch files.
   - **Interruptible waits only.** The deadlines bound waits that a signal can interrupt: pipe writes to a full stdout or stderr, sleeps, socket I/O, a provider that ignores cancellation. They cannot interrupt a syscall in uninterruptible sleep (for example `fsync` or `link` on a hung filesystem). The runner MUST tear down the exec or sandbox as the real upper bound.
   - **Post-startup alarm:** armed with `--grace-seconds` right after the request line is read. It bounds hardening (including the `prctl` warning) and validation. Firing exits 3. Rejection delivery re-arms it (firing exits 2).
   - **Bounded exit:** the process never runs normal interpreter shutdown. After the exit code is known, the CLI re-arms `alarm(grace)` (firing exits with that same code), flushes `sys.stdout`/`sys.stderr` best effort, and calls `os._exit`. atexit hooks and joins of non-daemon threads (for example from provider SDKs) are skipped. Our OTEL tracer is shut down inside the run's armed window. openai-agents tracing is disabled by the provider, so skipping its atexit flush loses nothing.
   - Any `BaseException` that escapes the run (for example `KeyboardInterrupt` from a provider) is logged under an armed alarm and maps to exit 3.
   - `os._exit` does not signal children.
   - **Hard deadline:** armed after validation at `ceil(timeout_ms/1000) + grace` seconds (`--grace-seconds`, default 30). It bounds the run, including providers that ignore cancellation. Firing exits 124.
   - **Finalization deadline:** when the result is ready, `alarm(grace)` replaces the hard deadline (no gap). It bounds publication (temp write, `fsync`, link) and delivery of the result line. Firing exits 124 if publication has not been recorded as complete, else 4.
   - Publication and exit classification cannot be committed atomically: a deadline can land just after `result.json` is linked. So:
     - exit 0 promises a complete `result.json` and a delivered result line;
     - exit 4 promises a complete `result.json` (delivery failed after publication);
     - every other exit (2 aside, see rule 10) makes no promise about absence: `result.json` may be missing or may be complete, and a `.result.json.<pid>` temp file may remain. `result.json` is never partial (it only appears via `link` of an fsynced file).
   - The runner MUST validate any `result.json` as untrusted data and MUST NOT infer success from its existence alone.
10. **Rejections.** Exit 2, with at most one result line (delivery bounded below) and no result file:
    - stdin over 1 MiB → 413 `{"error"}`
    - invalid JSON or schema → 422 `{"error","detail"}`. `detail` holds the pydantic errors without input values.
    - invalid `runDir` → 422 `{"error"}`
    Rejections happen before the provider is constructed. `detail` keeps at most 20 errors, without context, and truncates `loc` strings to 100 characters, so the line stays small. Rejection delivery runs under a `--grace-seconds` alarm; if stdout is not drained in time the CLI exits 2 (the line may be missing or truncated).
11. **Exit codes.**

    | Code | Meaning |
    |---|---|
    | 0 | Result file written and result line delivered, any `status_code` |
    | 1 | Python failed before `main()` ran (for example an import error, or an older image without `lightspeed_agentic.step`). Not produced by the CLI itself. |
    | 2 | Request rejected (rule 10: one result line, possibly undelivered if stdout was not drained), or invalid command-line arguments (argparse usage on stderr, no result line). No result file. |
    | 3 | Internal error, no result line (no promise about `result.json`, rule 9): startup failure (stdin/stdout fd setup, stdin closed, hardening), post-startup alarm, provider or config construction, pid/result file write, non-strict result JSON, any escaping `BaseException` |
    | 4 | `result.json` complete, but the result line was not delivered: stdout closed, or not drained before the finalization deadline |
    | 124 | Hard or finalization deadline before publication was recorded complete (no promise about `result.json`, rule 9) |
    | 137 / 143 (signal) | Killed |

12. **Secrets.** The CLI reads no secrets from argv or stdin. Provider configuration and credentials come from the same env as `/run`.
13. **Process hardening.** After reading the request the CLI points fd 0 at `/dev/null`, so children never inherit the request pipe. On Linux it calls `prctl(PR_SET_DUMPABLE, 0)` (failure is logged as a warning, not fatal), which blocks same-uid ptrace and `/proc/<pid>/fd` access to the CLI process itself. `execve` resets this for child processes.

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

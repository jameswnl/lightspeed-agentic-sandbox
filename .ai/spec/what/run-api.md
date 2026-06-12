# Behavioral spec: HTTP run API

Audience: AI agents (Claude). Precision over narrative.

Cross-references: provider behavior and events → `provider-contract.md`. Env defaults and ports → `configuration.md`.

## Behavioral Rules

1. **Operator integration boundary.** The Kubernetes operator (workflow engine) invokes the sandbox over HTTP using `POST /v1/agent/run` with a JSON body matching `RunRequest`. The sandbox returns `RunResponse` JSON. The operator carries step semantics primarily via `query` (template-rendered prompt), `outputSchema`, and `context`. The operator sends `systemPrompt` as empty; the sandbox applies a default persona when `systemPrompt` is empty or omitted (see rule 5). The sandbox does not interpret workflow phase names.

2. **Route mounting.** Agent routes are mounted under the path prefix `/v1/agent` on the FastAPI application. Probe routes (`/health`, `/ready`) are **not** under that prefix.

3. **Canonical run endpoint.** `POST /v1/agent/run` accepts `RunRequest` and returns `RunResponse`.

4. **RunRequest — `query` (required).** User task text. When `context` is present, the handler prepends a formatted context block to this text before sending the combined string to the provider (see rules 12–16).

5. **RunRequest — `systemPrompt`.** Optional. When omitted or null, the handler substitutes a fixed default assistant persona string.

6. **RunRequest — `outputSchema`.** Optional JSON-object schema. When present, forwarded to the provider as structured-output hints (see `provider-contract.md`). The HTTP response still follows `RunResponse` shaping rules (rules 18–22).

7. **RunRequest — `context`.** Optional object. When present, must be formatted by the rules in 12–16; unknown keys are ignored if not read by the formatter.

8. **RunRequest — `timeout_ms`.** Optional. When set, caps wall-clock time for consuming the provider event stream until the first `result` event. When omitted, a router-level default timeout applies (see `configuration.md`).

9. **Per-run spend ceiling.** The route passes a fixed USD budget cap into provider options. This cap is **not** configurable via `RunRequest`.

10. **GET /health.** Returns a JSON object `{ "status": "ok" }` when the process is up (not mounted under `/v1/agent`).

11. **GET /ready.** Readiness probe (not under `/v1/agent`). Returns HTTP 200 with `{ "status": "ok" }` when all checks pass; HTTP 503 with `{ "status": "error", "checks": { ... } }` when any check fails. Checks and semantics: `health-probes.md`.

12. **Context prefix — envelope.** When `context` is non-empty, the formatter produces a block that starts with a fixed marker line, ends with a closing marker line, and is prepended to `query` with separating newlines.

13. **Context — `targetNamespaces`.** When present and non-empty (list), include a line listing target namespaces as a comma-separated join.

14. **Context — `attempt`.** When present (any), include a line labeling the attempt with placeholder text for the maximum (literal substring `of max` in the line; the formatter does not inject the max value).

15. **Context — `previousAttempts`.** When present and non-empty (iterable of objects), include a header line then one bullet line per entry with attempt index and optional `failureReason`.

16. **Context — `approvedOption`.** When present and non-empty (object), append a bounded block: title, diagnosis root cause, proposal description, risk, reversibility, and optional action list with type and description; surround with explicit “approved remediation” and “do not exceed listed actions” banners.

17. **Stream consumption.** The handler iterates the provider async iterator until a `result` event; earlier events are logged but do not terminate the request. See `provider-contract.md` for event types.

18. **RunResponse — core fields.** Every response includes `success` (boolean) and `summary` (string). Additional keys are allowed on the response object.

19. **Structured agent output.** When the final `result` text is JSON parsing as an object, the handler builds `RunResponse` with `success` from that object’s `success` key defaulting to true when absent, `summary` from `summary` defaulting to the raw result text when absent, and merges remaining keys as extra top-level fields.

20. **Text fallback.** When the final `result` text is not a JSON object (parse failure or non-object JSON), the handler returns `success=true` and `summary` equal to the full result text with no extra keys from parsing.

21. **Timeout.** When waiting for the provider exceeds the effective timeout, the handler returns `success=false` and a summary string that states timeout and includes the timeout duration in milliseconds.

22. **Agent errors.** On any other exception during the provider call, the handler returns `success=false` and a summary prefixed with a fixed agent-error label and the exception message.

23. **Empty result.** When the stream ends without non-empty final `result` text, the handler returns `success=false` with a fixed empty-response summary.

24. **Allowed tools.** The route passes the default allowed-tools list into provider options; callers cannot override via `RunRequest` (see `provider-contract.md`).

## Configuration Surface

| Mechanism | Purpose |
|-----------|---------|
| `RunRequest.timeout_ms` | Per-request wall-clock limit for waiting on the first `result` event (milliseconds). |
| Router `default_timeout_ms` | Used when `timeout_ms` is omitted (see `configuration.md`). |
| `LIGHTSPEED_SKILLS_DIR` | Working directory / skill root forwarded as provider `cwd` (see `configuration.md`). |

## Constraints

- The handler does not expose `max_turns`, model id, provider id, or tool allowlists on `RunRequest`; those are fixed or environment-driven per `configuration.md` and router construction.
- Streaming to the HTTP client is out of scope for `POST /run`; provider streaming may be used internally only if the adapter enables it (see `how/provider-architecture.md`).

## Planned Changes

- Operator payload may later include `llm` and `allowedTools` per target architecture docs; sandbox route does not read them today. [PLANNED: OLS-3033]
- TLS, network policy, and ingress hardening for the sandbox service. [PLANNED: OLS-3038–OLS-3043]

## Verification

Harness scope (live vs unit, run modes, flake policy):
[e2e-testing.md](e2e-testing.md).

Two layers:

1. **Unit tests** (`tests/test_routes.py`) — mocked provider, deterministic handler
   behavior (timeouts, empty result, response shaping). Preferred for rules 21 and 23.
2. **Container BDD** (`tests/e2e/features/`, `scripts/e2e-containers.sh`) — live
   `/v1/agent/run` against one sandbox container per process with real credentials.

Rules **10–11** (`/health`, `/ready`) are verified under `health-probes.md`, not here.

| Artifact | Rules exercised | Notes |
|----------|-----------------|-------|
| [structured_output.feature](../../../tests/e2e/features/structured_output.feature) | 3, 6, 18–20 | Live structured output and text fallback; adversarial schema stays HTTP 200 with envelope (rule 22 not triggered) |
| [skills.feature](../../../tests/e2e/features/skills.feature) | 3, 18–20 | `/run` success paths with skills mounted (see `provider-contract.md`) |
| [test_routes.py](../../../tests/test_routes.py) | 3, 5, 6, 8, 18–21, 23 | Mocked provider: `systemPrompt`, `outputSchema`, `timeout_ms`, timeout failure, empty result, text fallback |
| [sandbox_e2e.feature](../../../tests/e2e/features/sandbox_e2e.feature) (Context prefix) | 4, 7, 12–16 | Live **targetNamespaces**, **previousAttempts**, and **approvedOption** echo via structured output; exact prefix strings in [test_routes.py](../../../tests/test_routes.py) |
| [sandbox_e2e.feature](../../../tests/e2e/features/sandbox_e2e.feature) (Run error handling) | 21 | Live **timeout** only (`timeout_ms=1` → HTTP 200, `success=false`, timed-out summary). Rules 22–23 and no-500 adversarial path: `test_routes.py`, `structured_output.feature` |

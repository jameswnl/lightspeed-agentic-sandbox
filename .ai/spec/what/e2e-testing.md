# E2E container BDD harness

Meta-spec for **how** live end-to-end tests run in this repository. Behavioral
rules for `/run`, probes, and providers live in the other `what/` specs; this
document maps scenarios to those rules and records spike decisions for
[OLS-3220](https://redhat.atlassian.net/browse/OLS-3220)

## Spike findings (OLS-3220)

Investigation goal: add BDD coverage for health probes, context prefix, and run
error handling without flaky live LLM assertions.

### Feasible in live container BDD

| Area | Approach | Artifact |
|------|----------|----------|
| Liveness / readiness happy path | Direct HTTP GET; deterministic status and JSON shape | [sandbox_e2e.feature](../../../tests/e2e/features/sandbox_e2e.feature) |
| Run timeout envelope | `timeout_ms=1` on a long-running query → HTTP 200, `success=false` | [sandbox_e2e.feature](../../../tests/e2e/features/sandbox_e2e.feature) |
| Context reaches the model | **Structured echo**: prepared `context` (`targetNamespaces`, `previousAttempts`, `approvedOption`) + `outputSchema`; model echoes back as response fields (`namespaces`, `firstFailureReason`, `approvedTitle`/`rootCause`) | [sandbox_e2e.feature](../../../tests/e2e/features/sandbox_e2e.feature) |
| Structured output / skills | Existing scenarios unchanged | [structured_output.feature](../../../tests/e2e/features/structured_output.feature), [skills.feature](../../../tests/e2e/features/skills.feature) |

Context proof is **semantic** (model output reflects injected context), not
inspection of the composed `[context]` prefix string. Exact prefix formatting
belongs in unit tests.

### Not feasible / intentionally unit-only

| Area | Reason | Artifact |
|------|--------|----------|
| Exact `[context]` prefix text | Deterministic formatting; no need for live LLM | [test_routes.py](../../../tests/test_routes.py) (`_format_context_prefix`) |
| Empty provider result (run-api rule 23) | Requires mocked provider; unreliable with live models | [test_routes.py](../../../tests/test_routes.py) |
| `/ready` 503 when credentials missing | Needs deliberately misconfigured runtime; covered without live network | [test_ready.py](../../../tests/test_ready.py) |
| Readiness rule R3 (MCP reachability) | MCP not implemented | — |
| HTTP 500 on adversarial schema (rule 22) | Live suite asserts HTTP 200 + envelope instead | [structured_output.feature](../../../tests/e2e/features/structured_output.feature), [test_routes.py](../../../tests/test_routes.py) |

### Design decisions

- **One feature file** for OLS-3220 scenarios: `sandbox_e2e.feature` (probes,
  timeout, context echo) instead of three separate files. Same scenarios, less
  glue duplication.
- **`runner.py` extensions**: `get_json()` for probe GETs; `context=` on
  `run_query()` for POST `/v1/agent/run`.
- **Two run modes** (see [Harness](#harness) below): container image (local dev)
  and `--prow-host` (host uvicorn for OpenShift CI without podman).
- **Skill token output**: `E2E_OUTPUT_DIR` is a host tmpdir; OpenAI
  `UnixLocalSandbox` only allows writes under the skills tree unless
  `extra_path_grants` includes that path (see `openai.py`). Tmpdir is removed
  after pytest; optional copy to `ARTIFACT_DIR` on Prow.

- **Multi-provider matrix** — ticket AC requires at least one provider; OpenAI
  validated on `--prow-host`. Claude/Gemini optional before merge.

## Relationship to behavioral specs

| Behavioral spec | This harness exercises |
|-----------------|------------------------|
| [run-api.md](run-api.md) | Timeout (rule 21), context wiring (rules 4, 7, 12–16); rules 22–23 via unit tests |
| [health-probes.md](health-probes.md) | `/health`, `/ready` happy path (rules 10–11) |
| [provider-contract.md](provider-contract.md) | Structured output and skills via existing feature files |
| [configuration.md](configuration.md) | Model/env resolution implicit in container and prow-host startup |

Do **not** duplicate behavioral rules here. When adding a scenario, update the
relevant `what/` spec Verification table first, then the feature file.

## Harness

### Layout

```text
tests/e2e/
├── features/           # Gherkin scenarios
├── steps/              # given / when / then step definitions
├── runner.py           # HTTP client (GET probes, POST /run)
├── conftest.py         # fixtures: server_url, e2e_output_dir, bdd_context
├── credentials.py      # preflight credential checks per provider
├── config.env          # default models for e2e (sourced in clean env)
└── pytest.ini          # e2e collection config

scripts/e2e-containers.sh   # start sandbox, export env, run pytest
```

### Run modes

**Container (default)** — requires podman or docker:

```bash
make e2e openai
# or: bash scripts/e2e-containers.sh openai [model-override]
```

Builds or uses `IMAGE`, mounts skills workspace and tmp output dir, runs one
provider per process, exports `SANDBOX_SERVICE_URL` and `E2E_PROVIDER` for pytest.

**Prow host** — no container runtime; uvicorn on the host (OpenShift CI):

```bash
E2E_SKIP_INSTALL=1 bash scripts/e2e-containers.sh --prow-host openai
# optional model: ... --prow-host openai gpt-5-mini
```

Uses `tests/e2e/config.env` models in a clean env (avoids host shell pollution
e.g. `OPENAI_MODEL=claude-…`). LLM credentials may be copied under
`.e2e/llm-credentials` when `/var/run/secrets` is not writable.

### Environment exports

| Variable | Set by | Purpose |
|----------|--------|---------|
| `SANDBOX_SERVICE_URL` | `e2e-containers.sh` | Base URL for pytest (app root, not `/v1/agent`) |
| `E2E_PROVIDER` | `e2e-containers.sh` | Provider name for credential checks / logging |
| `E2E_OUTPUT_DIR` | `e2e-containers.sh` | Host path where skill tools write `.e2e_token` |
| `E2E_ARGS` | operator | Extra pytest args (e.g. `-v`, `-k`) |
| `ARTIFACT_DIR` | Prow | Token output copied before tmp cleanup; pytest tee'd to `e2e-<provider>-pytest.log` and `e2e-<provider>-summary.txt` (alongside `junit_e2e.xml` from `E2E_ARGS`) |

### Flake policy

- Prefer **deterministic HTTP assertions** (status codes, envelope fields) over
  free-text LLM output when possible.
- Live context scenarios use **structured output** with strict echo instructions
  in the system prompt and schema.
- Scenarios that depend on provider timing (timeout with `timeout_ms=1`) assert
  the **response envelope**, not whether the provider finished mid-flight.
- Do not add live tests that require missing credentials, broken endpoints, or
  empty model output unless the harness gains a fake-provider mode.

## Verification map

Feature files and unit tests are also listed under each behavioral spec. Summary:

| Feature file | Primary spec | Scenarios |
|--------------|--------------|-----------|
| [sandbox_e2e.feature](../../../tests/e2e/features/sandbox_e2e.feature) | run-api, health-probes | Probes, timeout, context echo |
| [structured_output.feature](../../../tests/e2e/features/structured_output.feature) | run-api, provider-contract | JSON schema, text fallback, adversarial schema |
| [skills.feature](../../../tests/e2e/features/skills.feature) | provider-contract | Skills mount, echo-token skill, nonskill query |

Unit tests: [test_routes.py](../../../tests/test_routes.py),
[test_health.py](../../../tests/test_health.py),
[test_ready.py](../../../tests/test_ready.py).

## Commands

```bash
make install-all          # providers + e2e extras (first time)
make test                 # unit only; no credentials
make e2e openai           # live BDD, container mode
E2E_SKIP_INSTALL=1 E2E_ARGS="-v" bash scripts/e2e-containers.sh --prow-host openai
```

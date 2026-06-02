# Behavioral spec: health probes

Origin: [OLS-3058](https://redhat.atlassian.net/browse/OLS-3058) — sandbox failure modes audit.

Cross-references: `configuration.md` (env vars, port), `run-api.md` (route mounting).

## Principles

Sandbox pods are ephemeral one-shot workers. Probes confirm the pod can accept work; all real failures surface on `POST /v1/agent/run` where the operator handles them. Probes MUST NOT make authenticated API calls or spend tokens.

## Endpoints

### `GET /health` (liveness)

Existing endpoint, unchanged. Returns `{"status": "ok"}` if uvicorn is alive. No subsystem checks.

### `GET /ready` (readiness, new)

Returns HTTP 200 when all checks pass, HTTP 503 when any check fails. Not under `/v1/agent`.

**Healthy:** `{"status": "ok"}`

**Unhealthy:**
```json
{
  "status": "error",
  "checks": {
    "provider_env": "ok",
    "provider_endpoint": "error: connection refused"
  }
}
```

## Readiness Checks

**R1 — Credential env.** Check that the expected credential env var(s) for the resolved backend are set and non-empty. The expected vars are backend-specific (carried in `ResolvedSDK.expected_envs`), not SDK-level. Does NOT validate the key's value.

| Backend | Required env var(s) |
|---------|-------------------|
| `anthropic` (direct) | `ANTHROPIC_API_KEY` |
| `vertex/*` (all model providers) | `GOOGLE_APPLICATION_CREDENTIALS` |
| `openai` (direct) | `OPENAI_API_KEY` |
| `azure` | `AZURE_OPENAI_API_KEY` |
| `bedrock` | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` |

**R2 — Provider endpoint reachable.** Unauthenticated HTTP GET to the provider base URL. 3-second timeout. Any HTTP response (including 4xx) = reachable. Timeout or connection error = not ready.

| Provider | Probe URL |
|----------|----------|
| `claude` | `https://api.anthropic.com/` |
| `gemini` | `https://generativelanguage.googleapis.com/` |
| `openai` | `OPENAI_BASE_URL` or `https://api.openai.com/` |

**R3 — MCP server reachability.** Same pattern as R2, for each configured MCP endpoint. [PLANNED: when MCP support lands]

## Recommended Probe Config

```yaml
livenessProbe:
  httpGet: { path: /health, port: 8080 }
  periodSeconds: 10
  timeoutSeconds: 3
  failureThreshold: 3

readinessProbe:
  httpGet: { path: /ready, port: 8080 }
  initialDelaySeconds: 2
  periodSeconds: 5
  timeoutSeconds: 5
  failureThreshold: 2
```

## Out of Scope for Probes

Credential validity, skills content, model availability, tool execution — all caught by `/run` and handled by the operator.

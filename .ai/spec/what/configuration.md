# Behavioral spec: configuration, environment, deployment

Audience: AI agents (Claude). Precision over narrative.

Cross-references: how options are consumed in code → `how/provider-architecture.md`. HTTP fields → `run-api.md`. Provider options → `provider-contract.md`.

## Behavioral Rules

1. **Provider selection.** Process environment variable `LIGHTSPEED_AGENT_PROVIDER` selects the backend. Supported logical values: `claude`, `gemini`, `openai`, `deepagents`, `deepagents-claude`, `deepagents-gemini`, `deepagents-openai`. The three `deepagents-*` values select the same Deep Agents implementation; unknown values are rejected at startup when constructing the provider.

2. **Default provider.** When `LIGHTSPEED_AGENT_PROVIDER` is unset, the provider defaults to Claude.

3. **Model resolution — Claude.** Read `ANTHROPIC_MODEL`; if unset, use the package default model constant for Claude.

4. **Model resolution — Gemini.** Read `GEMINI_MODEL`; if unset, fall back to the same package default model constant used for Claude-branded defaults.

5. **Model resolution — OpenAI.** Read `OPENAI_MODEL`; if unset, fall back to that package default model constant.

6. **Model resolution — Deep Agents.** Read `DEEPAGENTS_MODEL`; if unset, fall back to that package default model constant.

7. **Router override.** Callers of the library `build_router` may pass an explicit `model` string; when provided, it overrides environment-based resolution for that router instance.

8. **Skills directory.** `LIGHTSPEED_SKILLS_DIR` sets the filesystem root for skills and provider `cwd`. Default when unset is the container default path under `/app`.

9. **Provider credentials.** API authentication uses the conventional env vars expected by each vendor SDK (Anthropic, Google/Gemini, OpenAI). The sandbox does not define alternate names beyond what adapters read for routing (e.g., Gemini API key fallbacks).

10. **Vertex / Google GenAI.** `GOOGLE_GENAI_USE_VERTEXAI` toggles Vertex behavior for the Gemini adapter (tool composition rules per `provider-contract.md`).

11. **OpenAI base URL.** `OPENAI_BASE_URL` overrides the OpenAI client base URL when set.

12. **Deep Agents Vertex Claude.** `CLAUDE_CODE_USE_VERTEX` gates use of Vertex-hosted Claude via project and region env vars; project and region strings are read from `ANTHROPIC_VERTEX_PROJECT_ID` and `CLOUD_ML_REGION`.

13. **Router defaults — `max_turns`.** The router supplies a built-in default maximum turn count to provider options when routes are registered (not exposed on `RunRequest`).

14. **Router defaults — `default_timeout_ms`.** The router supplies a built-in default milliseconds timeout for the run handler when `RunRequest.timeout_ms` is null.

15. **Process entry.** The container process invokes Uvicorn serving the FastAPI app on TCP port `8080` on all interfaces.

16. **Container filesystem layout.** A read-only skills mount path, a writable per-pod workspace path under system temp, and a writable home directory path for the non-root runtime user are provisioned with ownership for that UID.

17. **Python load path.** Runtime sets process environment so application source under `/app` and installed site-packages are on `PYTHONPATH` as defined in the image.

18. **Hermetic / Konflux build inputs.** Release images are built with network isolation after prefetch: per-architecture Python requirements files with hashes, RPM lockfile input, generic binary lockfile for oc/kubectl/ripgrep/dumb-init, and npm lockfile for the Claude Code CLI. Regeneration of those artifacts is via the project automation commands (see implementation notes in `how/provider-architecture.md`).

19. **Non-hermetic fallback.** When prefetch directories are absent, the container build recipe may fetch selected binaries from external URLs for developer builds.

20. **System packages — minimum expectations.** Runtime image includes Bash, Git, OpenShift CLI (`oc`), Kubernetes CLI (`kubectl`), ripgrep, Node.js (Claude Code CLI), and supporting OS utilities for debugging and archives per the container recipe.

## Configuration Surface

| Variable / field | Role |
|------------------|------|
| `LIGHTSPEED_AGENT_PROVIDER` | Selects agent backend (see rule 1). |
| `ANTHROPIC_MODEL`, `GEMINI_MODEL`, `OPENAI_MODEL`, `DEEPAGENTS_MODEL` | Per-provider model ID override. |
| `LIGHTSPEED_SKILLS_DIR` | Skill root and provider working directory default. |
| `ANTHROPIC_API_KEY` | Claude SDK credential. |
| `GOOGLE_API_KEY`, `GEMINI_API_KEY` | Google GenAI credential (Gemini / routing in Deep Agents). |
| `OPENAI_API_KEY` | OpenAI SDK credential. |
| `GOOGLE_GENAI_USE_VERTEXAI` | Vertex mode for Gemini adapter. |
| `ANTHROPIC_VERTEX_PROJECT_ID`, `CLOUD_ML_REGION` | Vertex project/region for Deep Agents Claude path. |
| `CLAUDE_CODE_USE_VERTEX` | Enables Vertex Claude path in Deep Agents when set to sentinel value `1`. |
| `OPENAI_BASE_URL` | OpenAI-compatible endpoint override. |
| `build_router(..., skills_dir=..., model=..., max_turns=..., default_timeout_ms=...)` | Library-level defaults when embedding the router. |

## Constraints

- `RunRequest` does not carry provider name, model, max turns, or budget; changing those requires env vars, router constructor args, or future API extensions.
- Optional Python extras gate which provider SDKs are installed in a given environment; the image recipe installs all extras.

## Planned Changes

- TLS termination, mTLS, and network policies for operator-to-sandbox traffic. [PLANNED: OLS-3038–OLS-3043]
- Readiness and startup probes distinct from simple liveness. [PLANNED: OLS-3058–OLS-3060]
- Konflux pipeline and lockfile policy updates as Red Hat platform requirements evolve. [PLANNED: OLS-2894]

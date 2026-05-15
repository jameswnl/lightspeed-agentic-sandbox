# Lightspeed Agentic Sandbox -- Specifications

These specs define the requirements, behaviors, and architecture for the lightspeed-agentic-sandbox. They are organized into two layers:

- **[`what/`](what/)** -- Behavioral rules: WHAT the system must do. Technology-neutral, testable assertions.
- **[`how/`](how/)** -- Architecture specs: HOW the current implementation is structured. Module boundaries, data flow, SDK integration.

## Scope

These specs cover the **lightspeed-agentic-sandbox** Python agent runtime only. The operator (which calls this runtime), console plugin, and skills packaging are separate projects with their own specs.

## Audience

AI agents (Claude). Specs optimize for precision, unambiguous rules, and machine-parseable structure.

## Quick Start

| I want to... | Read |
|--------------|------|
| Understand the /run API | `what/run-api.md` |
| Add or modify a provider | `what/provider-contract.md` + `how/provider-architecture.md` |
| Understand env vars and deployment | `what/configuration.md` |
| Navigate the codebase | `how/provider-architecture.md` |

## Conventions

- `[PLANNED: OLS-XXXX]` markers indicate existing rules about to change
- Environment variable names reference the actual env var (e.g., `LIGHTSPEED_AGENT_PROVIDER`)
- Internal constants are stated as behavioral rules without numeric values

## Project Context

This is the agent runtime that runs inside ephemeral sandbox pods. The operator sends requests to `POST /v1/agent/run` and receives structured JSON responses. The runtime wraps multiple LLM provider SDKs (Claude, Gemini, OpenAI, DeepAgents) behind a single interface.

Jira tracking: Feature OCPSTRAT-3095, Epic OLS-2894.

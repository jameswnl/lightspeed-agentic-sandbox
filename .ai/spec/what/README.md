# Behavioral Specifications (what/)

These specs define WHAT the sandbox runtime must do -- testable behavioral rules, configuration surface, constraints, and planned changes.

## Spec Index

| Spec | Description |
|------|-------------|
| [run-api.md](run-api.md) | POST /run endpoint: request/response shapes, context prefix, timeouts, error handling |
| [provider-contract.md](provider-contract.md) | AgentProvider ABC, event model, structured output, thin-adapter principle, skills delegation |
| [configuration.md](configuration.md) | Environment variables, provider selection, model resolution, container layout, build system |
| [health-probes.md](health-probes.md) | Liveness (`/health`) and readiness (`/ready`) endpoints, failure mode audit, probe configuration |
| [e2e-testing.md](e2e-testing.md) | Container BDD harness: run modes, live vs unit split, OLS-3220 spike findings |

Behavioral specs with a **Verification** section link rules to BDD feature files
under `tests/e2e/features/` and to unit tests where live e2e is unsuitable.
Harness scope and flake policy: [e2e-testing.md](e2e-testing.md).

## Relationship to how/ Specs

These `what/` specs define the behavioral contract. The [`how/` specs](../how/README.md) describe the current Python implementation and SDK integration details.

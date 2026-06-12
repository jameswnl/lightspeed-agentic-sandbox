"""Given steps — service, schemas, prompts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pytest_bdd import given

from credentials import require_credentials
from schemas_contract import (
    CONTEXT_APPROVED_OPTION_ECHO_SCHEMA,
    CONTEXT_NAMESPACES_ECHO_SCHEMA,
    CONTEXT_PREVIOUS_ATTEMPTS_ECHO_SCHEMA,
    ECHO_TOKEN_SCHEMA,
    FLAT_OUTPUT_SCHEMA,
    NESTED_OUTPUT_SCHEMA,
    STRICT_CONFLICT_SCHEMA,
)


@given("provider credentials are configured")
def provider_credentials_configured(provider_name: str) -> None:
    require_credentials(provider_name)


@given("the sandbox service is running")
def sandbox_running(server_url: str) -> None:
    assert server_url.startswith("http"), f"unexpected server URL: {server_url!r}"


@given("the sandbox service is running with skills")
def sandbox_running_with_skills(server_url: str, e2e_output_dir: Path | None) -> None:
    assert server_url.startswith("http"), f"unexpected server URL: {server_url!r}"
    assert e2e_output_dir is not None, (
        "E2E_OUTPUT_DIR not set — skills not mounted (run via scripts/e2e-containers.sh)"
    )


@given("a simple non-skill query has been prepared")
def prepare_simple_non_skill(bdd_context: dict[str, Any]) -> None:
    bdd_context["query"] = "In one sentence, name any primary color."
    bdd_context["output_schema"] = None


@given("a query that will exceed the timeout has been prepared")
def prepare_timeout_query(bdd_context: dict[str, Any]) -> None:
    """Any non-trivial prompt — live provider work exceeds timeout_ms=1."""
    bdd_context["query"] = (
        "Explain quantum computing in detail, including superposition and entanglement."
    )


_CONTEXT_TARGET_NAMESPACES = ["default", "kube-system"]


@given("a context with target namespaces and an echo output schema have been prepared")
def prepare_context_namespaces_echo(bdd_context: dict[str, Any]) -> None:
    bdd_context["context"] = {"targetNamespaces": _CONTEXT_TARGET_NAMESPACES}
    bdd_context["expected_namespaces"] = ", ".join(_CONTEXT_TARGET_NAMESPACES)
    bdd_context["output_schema"] = CONTEXT_NAMESPACES_ECHO_SCHEMA
    bdd_context["query"] = (
        "The user message contains a [context] block with Target namespaces. "
        "Return a single JSON object only (no markdown). "
        "Set success=true, summary='context-echo-ok', and set namespaces to the "
        "comma-separated namespace values from the 'Target namespaces:' line "
        "(values only, not the label)."
    )


_CONTEXT_PREVIOUS_ATTEMPTS = [
    {"attempt": 1, "failureReason": "timeout"},
    {"attempt": 2},
]


@given("a context with previous attempts and an echo output schema have been prepared")
def prepare_context_previous_attempts_echo(bdd_context: dict[str, Any]) -> None:
    bdd_context["context"] = {"previousAttempts": _CONTEXT_PREVIOUS_ATTEMPTS}
    bdd_context["expected_first_failure_reason"] = "timeout"
    bdd_context["output_schema"] = CONTEXT_PREVIOUS_ATTEMPTS_ECHO_SCHEMA
    bdd_context["query"] = (
        "The user message contains a [context] block with a Previous attempts section. "
        "Return a single JSON object only (no markdown). "
        "Set success=true, summary='context-echo-ok', and set firstFailureReason to the "
        "failure reason on Attempt 1 (value only, not the label or attempt number)."
    )


_CONTEXT_APPROVED_OPTION = {
    "title": "Restart deployment",
    "diagnosis": {"rootCause": "CrashLoopBackOff"},
    "proposal": {
        "description": "Roll out restart",
        "risk": "low",
        "reversible": True,
    },
}


@given("a context with approved option and an echo output schema have been prepared")
def prepare_context_approved_option_echo(bdd_context: dict[str, Any]) -> None:
    bdd_context["context"] = {"approvedOption": _CONTEXT_APPROVED_OPTION}
    bdd_context["expected_approved_title"] = _CONTEXT_APPROVED_OPTION["title"]
    bdd_context["expected_root_cause"] = _CONTEXT_APPROVED_OPTION["diagnosis"]["rootCause"]
    bdd_context["output_schema"] = CONTEXT_APPROVED_OPTION_ECHO_SCHEMA
    bdd_context["query"] = (
        "The user message contains a [context] block with an approved remediation section. "
        "Return a single JSON object only (no markdown). "
        "Set success=true, summary='context-echo-ok', approvedTitle to the remediation "
        "Title value, and rootCause to the Diagnosis root cause value (values only, "
        "not labels)."
    )


@given("the echo-token skill query has been prepared")
def prepare_echo_token(bdd_context: dict[str, Any]) -> None:
    bdd_context["system_prompt"] = (
        "You are an agent with shell access. When a skill provides a script, "
        "you must run that script via the shell tool and use its stdout JSON "
        "before producing your final answer. Never invent or placeholder token values."
    )
    bdd_context["query"] = (
        "Use the echo-token skill end-to-end:\n"
        "1. Load the echo-token skill.\n"
        "2. From the loaded skill directory (.agents/echo-token), run: bash tools/echo-token.sh\n"
        "3. Parse the JSON printed to stdout.\n"
        "4. Reply with a single JSON object only (no markdown): success=true, "
        "summary containing the token verbatim, token equal to the script token field, "
        "and status equal to the script status field.\n"
        "Do not reply until step 2 exits 0 and prints JSON."
    )
    bdd_context["output_schema"] = ECHO_TOKEN_SCHEMA


@given("a flat output schema with required fields has been prepared")
def prepare_flat(bdd_context: dict[str, Any]) -> None:
    bdd_context["output_schema"] = FLAT_OUTPUT_SCHEMA
    bdd_context["query"] = (
        "Respond with a single JSON object only (no markdown). "
        'Fields: success=true, summary="e2e-flat-ok", ticketId="E2E-STRUCT-001".'
    )


@given("a nested output schema has been prepared")
def prepare_nested(bdd_context: dict[str, Any]) -> None:
    bdd_context["output_schema"] = NESTED_OUTPUT_SCHEMA
    bdd_context["query"] = (
        "Respond with a single JSON object only (no markdown). "
        'success=true, summary="e2e-nested-ok", '
        'items=[{"name":"widget","count":1},{"name":"gadget","count":2}].'
    )


@given("no output schema will be sent")
def prepare_no_schema(bdd_context: dict[str, Any]) -> None:
    bdd_context["output_schema"] = None
    bdd_context["query"] = (
        "In one short sentence, name any primary color. Do not return JSON; plain text is fine."
    )


@given("an adversarial output schema and prompt have been prepared")
def prepare_adversarial(bdd_context: dict[str, Any]) -> None:
    bdd_context["output_schema"] = STRICT_CONFLICT_SCHEMA
    bdd_context["query"] = (
        "Reply with exactly the single word hello in plain text. "
        "Do not use JSON. Do not use markdown."
    )

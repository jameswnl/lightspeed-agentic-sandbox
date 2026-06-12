"""Then steps — HTTP and JSON assertions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import jsonschema
from pytest_bdd import then

from tests.e2e.runner import RunHttpResult

# SHA-256 of empty string — models sometimes fabricate this instead of running echo-token.sh
_EMPTY_STRING_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


@then("the response body status is ok")
def assert_body_status_ok(bdd_context: dict[str, Any]) -> None:
    """Assert probe JSON body has ``status: ok``."""
    body = bdd_context["response_body"]
    assert body.get("status") == "ok", body


@then("the HTTP response status code is 200")
def assert_status_200(bdd_context: dict[str, Any]) -> None:
    """Assert HTTP 200 with no transport error."""
    res: RunHttpResult = bdd_context["http_result"]
    assert res.error is None, f"transport error: {res.error}"
    assert res.status_code == 200, f"expected 200, got {res.status_code}: {res.raw_text[:500]}"


@then("the response includes success summary and ticketId fields")
def assert_flat_fields(bdd_context: dict[str, Any]) -> None:
    """Assert structured output includes success, summary, and ticketId."""
    body = bdd_context["response_body"]
    assert "success" in body
    assert "summary" in body
    assert isinstance(body["summary"], str)
    assert body.get("ticketId"), f"missing ticketId in {body!r}"


@then("the response JSON validates against the output schema")
def assert_jsonschema(bdd_context: dict[str, Any]) -> None:
    """Validate response body against the prepared output schema."""
    schema = bdd_context["output_schema"]
    body = bdd_context["response_body"]
    response_token = body.get("token", "")
    if isinstance(response_token, str) and response_token == _EMPTY_STRING_SHA256:
        raise AssertionError(
            "response token looks fabricated (empty-string SHA-256); "
            "run bash tools/echo-token.sh and use its stdout JSON"
        )
    jsonschema.validate(instance=body, schema=schema)


@then("the response has a non-empty summary")
def assert_nonempty_summary(bdd_context: dict[str, Any]) -> None:
    """Assert summary is a non-empty string."""
    body = bdd_context["response_body"]
    summary = body.get("summary", "")
    assert isinstance(summary, str), f"summary not a string: {body!r}"
    assert summary.strip(), f"summary missing/empty: {body!r}"


@then("success is true")
def assert_success_true(bdd_context: dict[str, Any]) -> None:
    """Assert RunResponse ``success`` is true."""
    body = bdd_context["response_body"]
    assert body.get("success") is True, body


@then("success is false")
def assert_success_false(bdd_context: dict[str, Any]) -> None:
    """Assert RunResponse ``success`` is false."""
    body = bdd_context["response_body"]
    assert body.get("success") is False, body


@then("the response namespaces field matches the prepared context")
def assert_namespaces_match_context(bdd_context: dict[str, Any]) -> None:
    """Assert echoed namespaces match targetNamespaces from prepared context."""
    body = bdd_context["response_body"]
    expected = bdd_context["expected_namespaces"]
    actual = body.get("namespaces", "")

    def _ns_parts(value: str) -> list[str]:
        return [part.strip() for part in value.split(",") if part.strip()]

    assert _ns_parts(actual) == _ns_parts(expected), (
        f"expected namespaces {expected!r}, got {actual!r} in {body!r}"
    )


@then("the response first failure reason matches the prepared context")
def assert_first_failure_reason_matches_context(bdd_context: dict[str, Any]) -> None:
    """Assert echoed firstFailureReason matches previousAttempts from prepared context."""
    body = bdd_context["response_body"]
    expected = bdd_context["expected_first_failure_reason"]
    actual = body.get("firstFailureReason", "")
    assert actual == expected, (
        f"expected firstFailureReason {expected!r}, got {actual!r} in {body!r}"
    )


@then("the response approved option fields match the prepared context")
def assert_approved_option_matches_context(bdd_context: dict[str, Any]) -> None:
    """Assert echoed approvedTitle and rootCause match approvedOption from context."""
    body = bdd_context["response_body"]
    expected_title = bdd_context["expected_approved_title"]
    expected_root_cause = bdd_context["expected_root_cause"]
    actual_title = body.get("approvedTitle", "")
    actual_root_cause = body.get("rootCause", "")
    assert actual_title == expected_title, (
        f"expected approvedTitle {expected_title!r}, got {actual_title!r} in {body!r}"
    )
    assert actual_root_cause == expected_root_cause, (
        f"expected rootCause {expected_root_cause!r}, got {actual_root_cause!r} in {body!r}"
    )


@then("the skill script wrote a token file to disk")
def assert_token_file(e2e_output_dir: Path | None, bdd_context: dict[str, Any]) -> None:
    """Assert the echo-token skill wrote ``.e2e_token`` under E2E_OUTPUT_DIR."""
    assert e2e_output_dir is not None, "E2E_OUTPUT_DIR not set"
    token_path = e2e_output_dir / ".e2e_token"
    assert token_path.exists(), (
        f"token file not found at {token_path}; "
        "the agent must run bash tools/echo-token.sh from .agents/echo-token"
    )
    token = token_path.read_text().strip()
    assert token, "token file is empty"
    bdd_context["token"] = token


@then("the response contains the generated token")
def assert_token_in_response(bdd_context: dict[str, Any]) -> None:
    """Assert the response body or summary includes the token from disk."""
    body = bdd_context["response_body"]
    token = bdd_context["token"]
    response_token = body.get("token", "")
    summary = body.get("summary", "")
    assert token in response_token or token in summary, (
        f"token {token!r} not found in response token={response_token!r} or summary={summary!r}"
    )


@then("the HTTP response status code is 200 and the envelope has success and summary")
def assert_200_envelope(bdd_context: dict[str, Any]) -> None:
    """Assert HTTP 200 and RunResponse envelope fields without schema validation."""
    res: RunHttpResult = bdd_context["http_result"]
    assert res.error is None, f"transport error: {res.error}"
    assert res.status_code == 200, f"expected 200, got {res.status_code}: {res.raw_text[:500]}"
    body = bdd_context["response_body"]
    assert "success" in body, body
    assert "summary" in body, body
    assert isinstance(body["summary"], str), body

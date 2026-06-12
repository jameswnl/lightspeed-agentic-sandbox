"""When steps — HTTP requests against the sandbox."""

from __future__ import annotations

from typing import Any

from pytest_bdd import when

from tests.e2e.runner import RunHttpResult, get_json


@when("I GET /health")
def get_health(bdd_context: dict[str, Any], server_url: str) -> None:
    """GET /health and store the response on the BDD context."""
    res: RunHttpResult = get_json(server_url, "/health")
    bdd_context["http_result"] = res
    bdd_context["response_body"] = res.body


@when("I GET /ready")
def get_ready(bdd_context: dict[str, Any], server_url: str) -> None:
    """GET /ready and store the response on the BDD context."""
    res: RunHttpResult = get_json(server_url, "/ready")
    bdd_context["http_result"] = res
    bdd_context["response_body"] = res.body


@when("I POST run with the prepared echo-token query")
def post_echo_token_query(bdd_context: dict[str, Any], run_runner: Any, provider_name: str) -> None:
    """POST /run for echo-token; OpenAI omits outputSchema so shell tools run first."""
    query = bdd_context["query"]
    kwargs: dict[str, Any] = {}
    if "system_prompt" in bdd_context:
        kwargs["system_prompt"] = bdd_context["system_prompt"]
    # Claude/Gemini wrap JSON in markdown fences without schema; OpenAI needs schema omitted.
    output_schema = None if provider_name == "openai" else bdd_context.get("output_schema")
    res: RunHttpResult = run_runner(query, output_schema=output_schema, **kwargs)
    bdd_context["http_result"] = res
    bdd_context["response_body"] = res.body


@when("I POST run with the prepared schema and query")
def post_with_schema(bdd_context: dict[str, Any], run_runner: Any) -> None:
    """POST /v1/agent/run using the prepared query and output schema."""
    schema = bdd_context.get("output_schema")
    query = bdd_context["query"]
    kwargs: dict[str, Any] = {}
    if "system_prompt" in bdd_context:
        kwargs["system_prompt"] = bdd_context["system_prompt"]
    res: RunHttpResult = run_runner(query, output_schema=schema, **kwargs)
    bdd_context["http_result"] = res
    bdd_context["response_body"] = res.body


@when("I POST run with the prepared query and no output schema")
def post_without_schema(bdd_context: dict[str, Any], run_runner: Any) -> None:
    """POST /v1/agent/run using the prepared query without an output schema."""
    query = bdd_context["query"]
    res: RunHttpResult = run_runner(query, output_schema=None)
    bdd_context["http_result"] = res
    bdd_context["response_body"] = res.body


@when("I POST run with timeout_ms 1")
def post_with_timeout_ms_1(bdd_context: dict[str, Any], run_runner: Any) -> None:
    """POST /v1/agent/run with a 1ms server-side timeout."""
    query = bdd_context["query"]
    res: RunHttpResult = run_runner(query, timeout_ms=1)
    bdd_context["http_result"] = res
    bdd_context["response_body"] = res.body


@when("I POST run with the prepared context and schema")
def post_with_context_and_schema(bdd_context: dict[str, Any], run_runner: Any) -> None:
    """POST /v1/agent/run with prepared context, output schema, and query."""
    res: RunHttpResult = run_runner(
        bdd_context["query"],
        output_schema=bdd_context["output_schema"],
        context=bdd_context["context"],
    )
    bdd_context["http_result"] = res
    bdd_context["response_body"] = res.body

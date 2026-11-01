"""Query endpoints — POST /run and GET /events.

POST /run: the operator sends {query, systemPrompt, outputSchema, context, timeout_ms}
and the agent runs the LLM and returns {success, summary, ...structured fields}.

GET /events: returns the JSONL event log written during agent runs.
"""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from lightspeed_agentic.routes.models import RunRequest, RunResponse
from lightspeed_agentic.types import AgentProvider


def register_query_routes(
    router: APIRouter,
    *,
    provider: AgentProvider,
    skills_dir: str,
    model: str,
    max_turns: int,
    default_timeout_ms: int,
    audit_enabled: bool = False,
) -> None:
    # Imported here: lightspeed_agentic.run imports routes.models, which loads this module.
    from lightspeed_agentic.run import execute_run

    async def run_endpoint(req: RunRequest, request: Request) -> JSONResponse:
        outcome = await execute_run(
            req,
            provider=provider,
            model=model,
            skills_dir=skills_dir,
            max_turns=max_turns,
            default_timeout_ms=default_timeout_ms,
            audit_enabled=audit_enabled,
            traceparent=request.headers.get("traceparent"),
        )
        return JSONResponse(status_code=outcome.status_code, content=outcome.body)

    router.add_api_route("/run", run_endpoint, methods=["POST"], response_model=RunResponse)

    def events_endpoint() -> PlainTextResponse:
        """Return the JSONL event log file contents."""
        path = os.environ.get("AGENT_EVENT_LOG")
        if path is None or not os.path.isfile(path):
            raise HTTPException(status_code=404, detail="Event log not found")
        with open(path, encoding="utf-8") as f:
            content = f.read()
        return PlainTextResponse(content=content, media_type="application/x-ndjson")

    router.add_api_route("/events", events_endpoint, methods=["GET"])

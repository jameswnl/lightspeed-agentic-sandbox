"""Deep Agents provider — wraps langchain-ai/deepagents.

Uses create_deep_agent() with LocalShellBackend for shell + filesystem access,
native skills loading, and LangGraph streaming for event mapping.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from collections.abc import AsyncIterator
from typing import Any, Literal

from lightspeed_agentic.tools import resolve_skills_dir
from lightspeed_agentic.types import (
    TOOL_INPUT_MAX_CHARS,
    TOOL_OUTPUT_MAX_CHARS,
    AgentProvider,
    ContentBlockStopEvent,
    ProviderEvent,
    ProviderQueryOptions,
    ResultEvent,
    TextDeltaEvent,
    ToolCallEvent,
    ToolResultEvent,
    stringify,
)

logger = logging.getLogger(__name__)

_JSON_SCHEMA_TYPE_MAP: dict[str, type[Any]] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
}


def _json_schema_to_pydantic(schema: dict[str, Any], name: str = "OutputModel") -> Any:
    import pydantic

    if "properties" not in schema:
        raise ValueError(f"Schema {name!r} missing 'properties'")

    props = schema["properties"]
    required = set(schema.get("required", []))
    fields: dict[str, Any] = {}

    for field_name, field_schema in props.items():
        field_type = _resolve_field_type(field_schema, field_name)
        if field_name in required:
            fields[field_name] = (field_type, ...)
        else:
            fields[field_name] = (field_type | None, None)

    return pydantic.create_model(name, **fields)


def _resolve_field_type(schema: dict[str, Any], name: str) -> Any:
    if "type" not in schema and "enum" not in schema:
        raise ValueError(f"Field {name!r} missing 'type'")

    json_type = schema.get("type", "string")

    if json_type == "object":
        return _json_schema_to_pydantic(schema, name.title().replace("_", ""))

    if json_type == "array":
        if "items" not in schema:
            raise ValueError(f"Array field {name!r} missing 'items'")
        return list[Any]

    if "enum" in schema:
        return Literal[tuple(schema["enum"])]

    return _JSON_SCHEMA_TYPE_MAP.get(json_type, str)


_model_cache: dict[str, Any] = {}


def _resolve_model(model: str) -> Any:
    """Map a bare model name to a chat model instance or provider:model string.

    Deepagents uses langchain's init_chat_model which needs a provider prefix
    for non-obvious model names (e.g. 'google_genai:gemini-2.5-flash').
    Bare 'claude-*' and 'gpt-*'/'o1*'/'o3*' are auto-detected by langchain.
    Vertex AI Claude requires a pre-built instance for project/location config.
    """
    if ":" in model:
        return model

    if os.environ.get("CLAUDE_CODE_USE_VERTEX") == "1" and model.startswith("claude"):
        if model not in _model_cache:
            from langchain_google_vertexai.model_garden import ChatAnthropicVertex

            _model_cache[model] = ChatAnthropicVertex(
                model_name=model,
                project=os.environ.get("ANTHROPIC_VERTEX_PROJECT_ID", ""),
                location=os.environ.get("CLOUD_ML_REGION", "us-east5"),
            )
        return _model_cache[model]

    if model.startswith("gemini"):
        if os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY"):
            return f"google_genai:{model}"
        return f"google_vertexai:{model}"

    return model


class DeepAgentsProvider(AgentProvider):
    @property
    def name(self) -> str:
        return "deepagents"

    async def query(self, options: ProviderQueryOptions) -> AsyncIterator[ProviderEvent]:
        from deepagents import create_deep_agent
        from deepagents.backends.local_shell import LocalShellBackend
        from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

        backend = LocalShellBackend(root_dir=options.cwd, inherit_env=True)
        skills_dir = resolve_skills_dir(options.cwd)

        model: Any = _resolve_model(options.model)

        agent_kwargs: dict[str, Any] = {
            "model": model,
            "system_prompt": options.system_prompt,
            "backend": backend,
            "skills": [skills_dir],
        }

        if options.output_schema:
            if isinstance(options.output_schema, dict):
                agent_kwargs["response_format"] = _json_schema_to_pydantic(options.output_schema)
            else:
                agent_kwargs["response_format"] = options.output_schema

        agent = create_deep_agent(**agent_kwargs)

        thread_id = f"ls-{uuid.uuid4().hex[:12]}"
        seen_count = 0
        result_text = ""
        total_input_tokens = 0
        total_output_tokens = 0

        async for chunk in agent.astream(
            {"messages": [HumanMessage(content=options.prompt)]},
            config={"configurable": {"thread_id": thread_id}},
            stream_mode="values",
        ):
            messages = chunk.get("messages", [])
            for msg in messages[seen_count:]:
                if isinstance(msg, AIMessage):
                    if msg.tool_calls:
                        for tc in msg.tool_calls:
                            yield ToolCallEvent(
                                name=tc.get("name", ""),
                                input=json.dumps(tc.get("args", {}))[:TOOL_INPUT_MAX_CHARS],
                            )
                    elif msg.content:
                        content = (
                            msg.content if isinstance(msg.content, str) else stringify(msg.content)
                        )
                        if content:
                            yield TextDeltaEvent(text=content)
                            result_text = content

                    usage = getattr(msg, "usage_metadata", None)
                    if usage:
                        total_input_tokens += usage.get("input_tokens", 0)
                        total_output_tokens += usage.get("output_tokens", 0)

                elif isinstance(msg, ToolMessage):
                    yield ToolResultEvent(
                        output=stringify(msg.content)[:TOOL_OUTPUT_MAX_CHARS],
                    )

            seen_count = len(messages)

        yield ContentBlockStopEvent()

        final_output = chunk.get("structured_response") if chunk else None
        if final_output is not None:
            result_text = stringify(final_output)

        yield ResultEvent(
            text=result_text,
            cost_usd=0,
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
        )

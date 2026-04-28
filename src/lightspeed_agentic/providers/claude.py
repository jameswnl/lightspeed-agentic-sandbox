"""Claude provider — wraps claude-agent-sdk.

Maps to lightspeed-agent/src/providers/claude.ts.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from lightspeed_agentic.types import (
    TOOL_INPUT_MAX_CHARS,
    TOOL_OUTPUT_MAX_CHARS,
    AgentProvider,
    ContentBlockStopEvent,
    ProviderEvent,
    ProviderQueryOptions,
    ResultEvent,
    TextDeltaEvent,
    ThinkingDeltaEvent,
    ToolCallEvent,
    ToolResultEvent,
    stringify,
)


class ClaudeProvider(AgentProvider):
    @property
    def name(self) -> str:
        return "claude"

    async def query(self, options: ProviderQueryOptions) -> AsyncIterator[ProviderEvent]:
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            ResultMessage,
            StreamEvent,
            query,
        )

        output_format: dict[str, object] | None = None
        if options.output_schema:
            output_format = {
                "type": "json_schema",
                "schema": options.output_schema,
            }

        sdk_options = ClaudeAgentOptions(
            model=options.model,
            max_turns=options.max_turns,
            max_budget_usd=options.max_budget_usd,
            system_prompt=options.system_prompt,
            allowed_tools=options.allowed_tools,
            permission_mode="bypassPermissions",
            cwd=options.cwd,
            skills="all",
            include_partial_messages=True,
            output_format=output_format,
        )

        async for msg in query(prompt=options.prompt, options=sdk_options):
            if isinstance(msg, StreamEvent):
                event = msg.event
                if event.get("type") == "content_block_delta":
                    delta = event.get("delta", {})
                    if delta.get("type") == "text_delta" and delta.get("text"):
                        yield TextDeltaEvent(text=delta["text"])
                    elif delta.get("type") == "thinking_delta" and delta.get("thinking"):
                        yield ThinkingDeltaEvent(thinking=delta["thinking"])
                elif event.get("type") == "content_block_stop":
                    yield ContentBlockStopEvent()
                continue

            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if getattr(block, "type", None) == "tool_use":
                        yield ToolCallEvent(
                            name=getattr(block, "name", ""),
                            input=json.dumps(getattr(block, "input", {}))[:TOOL_INPUT_MAX_CHARS],
                        )

            if getattr(msg, "type", None) == "tool":
                for block in getattr(msg, "content", []):
                    if getattr(block, "type", None) == "tool_result":
                        yield ToolResultEvent(
                            output=stringify(getattr(block, "content", ""))[:TOOL_OUTPUT_MAX_CHARS],
                        )

            if isinstance(msg, ResultMessage):
                structured = getattr(msg, "structured_output", None)
                text = (
                    stringify(structured)
                    if structured is not None
                    else (getattr(msg, "result", None) or "")
                )

                usage = getattr(msg, "usage", None)
                yield ResultEvent(
                    text=text,
                    cost_usd=getattr(msg, "total_cost_usd", 0) or 0,
                    input_tokens=getattr(usage, "input_tokens", 0) if usage else 0,
                    output_tokens=getattr(usage, "output_tokens", 0) if usage else 0,
                )

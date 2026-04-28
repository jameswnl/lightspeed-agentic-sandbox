"""OpenAI provider — wraps openai-agents SDK.

Uses SandboxAgent with native Shell, Filesystem, and Skills capabilities.
The SDK handles tool registration, skill discovery, and command execution.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:

    class AgentOutputSchemaBase:
        """Type-checker stub for the optional openai-agents base class."""

        pass

else:
    try:
        from agents.agent_output import AgentOutputSchemaBase
    except ImportError:

        class AgentOutputSchemaBase:  # pragma: no cover - optional SDK fallback
            """Fallback base so the module imports without the openai extra."""

            pass


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


class _RawJsonSchema(AgentOutputSchemaBase):
    """Wraps an operator-provided JSON schema dict for the openai-agents SDK.

    Strict mode is disabled because vLLM does not support constrained decoding.
    """

    def __init__(self, schema: dict[str, Any]) -> None:
        self._schema = schema

    def is_plain_text(self) -> bool:
        return False

    def name(self) -> str:
        return "raw_json_schema"

    def json_schema(self) -> dict[str, Any]:
        return self._schema

    def is_strict_json_schema(self) -> bool:
        return False

    def validate_json(self, json_str: str) -> Any:
        return json.loads(json_str)


_openai_initialized = False


def _ensure_openai_init() -> None:
    global _openai_initialized
    if _openai_initialized:
        return
    from agents import enable_verbose_stdout_logging
    from agents.tracing import set_tracing_disabled

    set_tracing_disabled(True)
    enable_verbose_stdout_logging()  # type: ignore[no-untyped-call]
    _openai_initialized = True


class OpenAIProvider(AgentProvider):
    _client: Any = None

    @property
    def name(self) -> str:
        return "openai"

    async def query(self, options: ProviderQueryOptions) -> AsyncIterator[ProviderEvent]:
        from agents import (
            RawResponsesStreamEvent,
            RunItemStreamEvent,
            Runner,
        )
        from agents.items import ToolCallItem, ToolCallOutputItem
        from agents.models.openai_responses import OpenAIResponsesModel
        from agents.run_config import RunConfig, SandboxRunConfig
        from agents.sandbox import SandboxAgent
        from agents.sandbox.capabilities import Filesystem, Shell, Skills
        from agents.sandbox.capabilities.skills import LocalDirLazySkillSource
        from agents.sandbox.entries import LocalDir
        from agents.sandbox.manifest import Manifest
        from agents.sandbox.sandboxes.unix_local import (
            UnixLocalSandboxClient,
        )
        from openai.types.responses import ResponseTextDeltaEvent

        _ensure_openai_init()

        if self._client is None:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(base_url=os.environ.get("OPENAI_BASE_URL"))
        model = OpenAIResponsesModel(model=options.model, openai_client=self._client)

        skills_dir = resolve_skills_dir(options.cwd)
        capabilities = [
            Shell(),
            Filesystem(),
            Skills(
                lazy_from=LocalDirLazySkillSource(
                    source=LocalDir(src=Path(skills_dir)),
                )
            ),
        ]

        manifest = Manifest(root=options.cwd)

        agent_kwargs: dict[str, Any] = {
            "name": "lightspeed",
            "instructions": options.system_prompt,
            "model": model,
            "capabilities": capabilities,
            "default_manifest": manifest,
        }

        if options.output_schema:
            agent_kwargs["output_type"] = _RawJsonSchema(options.output_schema)

        agent = SandboxAgent(**agent_kwargs)

        run_config = RunConfig(
            sandbox=SandboxRunConfig(
                client=UnixLocalSandboxClient(),
            ),
        )

        result = Runner.run_streamed(
            agent,
            options.prompt,
            max_turns=options.max_turns,
            run_config=run_config,
        )

        async for event in result.stream_events():
            if isinstance(event, RawResponsesStreamEvent):
                if isinstance(event.data, ResponseTextDeltaEvent) and event.data.delta:
                    yield TextDeltaEvent(text=event.data.delta)
            elif isinstance(event, RunItemStreamEvent):
                if isinstance(event.item, ToolCallItem):
                    raw = event.item.raw_item
                    name = (
                        getattr(raw, "name", None)
                        or (raw.get("name") if isinstance(raw, dict) else "")
                        or ""
                    )
                    args = getattr(raw, "arguments", None) or ""
                    yield ToolCallEvent(name=name, input=args[:TOOL_INPUT_MAX_CHARS])
                elif isinstance(event.item, ToolCallOutputItem):
                    yield ToolResultEvent(
                        output=stringify(event.item.output)[:TOOL_OUTPUT_MAX_CHARS]
                    )

        yield ContentBlockStopEvent()

        usage = getattr(result, "usage", None) or {}
        input_tokens = getattr(usage, "input_tokens", 0)
        output_tokens = getattr(usage, "output_tokens", 0)

        yield ResultEvent(
            text=stringify(result.final_output),
            cost_usd=0,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

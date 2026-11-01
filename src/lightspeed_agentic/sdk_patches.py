"""SDK workarounds applied by every entry point before a provider is built."""

from __future__ import annotations

import logging
import sys

logger = logging.getLogger(__name__)


def apply_sdk_patches() -> None:
    # Workaround: openai >= 2.45 makes InputTokensDetails.cache_write_tokens required,
    # but openai-agents callers that predate it (e.g. a 0.18 default_factory) omit it.
    try:
        from openai.types.responses import ResponseUsage
        from openai.types.responses.response_usage import InputTokensDetails
        from pydantic.dataclasses import rebuild_dataclass

        field = InputTokensDetails.model_fields.get("cache_write_tokens")
        if field is None or not field.is_required():
            return
        field.default = 0
        # Schemas already built embed the required field; rebuild the ones we use.
        InputTokensDetails.model_rebuild(force=True)
        ResponseUsage.model_rebuild(force=True)
        if (agents_usage := sys.modules.get("agents.usage")) is not None:
            rebuild_dataclass(agents_usage.Usage, force=True)
    except ImportError:
        pass  # openai-agents is an optional extra
    except Exception:
        logger.warning("Failed to apply openai cache_write_tokens workaround", exc_info=True)

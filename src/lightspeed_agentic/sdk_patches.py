"""SDK workarounds applied by every entry point before a provider is built."""

from __future__ import annotations


def apply_sdk_patches() -> None:
    # Workaround: openai-agents 0.18.0 default_factory omits cache_write_tokens.
    # Import from openai, not agents.usage: importing agents.usage builds the
    # agents Usage dataclass schema, which would then miss the default.
    try:
        from openai.types.responses.response_usage import InputTokensDetails

        field = InputTokensDetails.model_fields.get("cache_write_tokens")
        if field is not None and field.is_required():
            field.default = 0
    except Exception:  # noqa: S110 - openai-agents is an optional extra
        pass

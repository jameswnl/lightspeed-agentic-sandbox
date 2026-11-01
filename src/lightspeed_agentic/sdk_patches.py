"""SDK workarounds applied by every entry point before a provider is built."""

from __future__ import annotations


def apply_sdk_patches() -> None:
    # Workaround: openai-agents 0.18.0 default_factory omits cache_write_tokens
    try:
        from agents.usage import InputTokensDetails  # type: ignore[attr-defined]

        if "cache_write_tokens" in InputTokensDetails.model_fields:
            InputTokensDetails.model_fields["cache_write_tokens"].default = 0
    except Exception:  # noqa: S110 - openai-agents is an optional extra
        pass

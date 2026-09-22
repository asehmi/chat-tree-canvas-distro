# Copyright (c) 2026 Arvindra Sehmi
# Licensed under the MIT License — see LICENSE for details.

"""LLM provider clients for the insecure agent provider.

Three provider clients — `anthropic_client.py`, `gemini_client.py`,
`openrouter_client.py` — sharing one `generate_response_stream(
system_instruction, prompt, tools=None, tool_handler=None)` shape (plus a
non-streaming `generate_response`), so `insecure_agent_provider.py` doesn't
need provider-specific branching beyond picking which class to
instantiate. `tools.py` defines the one tool (`run_python`) every
provider's agentic loop can call, and the provider-agnostic `ToolCall`/
`ToolCallLoopGuard` types they all share.

`get_llm_client()` below is that "picking which class to instantiate"
step, driven by `LLM_PROVIDER` (.env). This is deliberately a class-select
factory, not a base-url-swap pattern (one OpenAI-compatible client
repointed at a different base URL) — that only works because every
provider it targets speaks the OpenAI-compatible chat completions API.
Anthropic and Gemini don't, so their SDKs are genuinely different
integrations here, not just a different base URL.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .anthropic_client import AnthropicClient
    from .gemini_client import GeminiClient
    from .openrouter_client import OpenRouterClient

_PROVIDERS = ("anthropic", "gemini", "openrouter")


def get_llm_client() -> "AnthropicClient | GeminiClient | OpenRouterClient":  # noqa: UP037
    """The provider client the server should call — chosen by
    `LLM_PROVIDER`, defaulting to Anthropic. Each client class is imported
    lazily inside its own branch rather than at module load, so picking
    one provider doesn't require every provider's SDK to be installed
    (e.g. `google-genai` need not be installed just to run Anthropic-only).

    An optional `<PROVIDER>_MODEL` env var (e.g. `ANTHROPIC_MODEL`,
    `OPENROUTER_MODEL`) overrides that client's own default — model
    catalogs and slugs drift, and this is a giveaway meant to keep working
    without a code change when they do.
    """
    provider = os.getenv("LLM_PROVIDER", "anthropic").strip().lower()

    if provider == "anthropic":
        from .anthropic_client import AnthropicClient

        model = os.getenv("ANTHROPIC_MODEL")
        return AnthropicClient(model=model) if model else AnthropicClient()
    if provider == "gemini":
        from .gemini_client import GeminiClient

        model = os.getenv("GEMINI_MODEL")
        return GeminiClient(model=model) if model else GeminiClient()
    if provider == "openrouter":
        from .openrouter_client import OpenRouterClient

        model = os.getenv("OPENROUTER_MODEL")
        return OpenRouterClient(model=model) if model else OpenRouterClient()

    raise ValueError(
        f"Unknown LLM_PROVIDER: {provider!r} — must be one of {_PROVIDERS}"
    )

# Copyright (c) 2026 Arvindra Sehmi
# Licensed under the MIT License — see LICENSE for details.

"""Provider registry: CHAT_API_PROVIDER selects the chat backend.

Built-in providers:
  reference  the canonical minimal protocol on :9000 (mock_provider.py
             implements it end-to-end; insecure_agent_provider.py is a
             real, LLM-backed implementation of the same protocol)

To add your own: subclass ChatProvider (base.py), give it a unique `name`,
and add the class to _REGISTRY. Full guide: docs/PROVIDERS.md.
"""

import os
from functools import lru_cache

from chat_tree.providers.base import ChatProvider
from chat_tree.providers.reference import ReferenceProvider

_REGISTRY: dict[str, type[ChatProvider]] = {
    ReferenceProvider.name: ReferenceProvider,
}


@lru_cache(maxsize=1)
def get_provider() -> ChatProvider:
    """The provider named by CHAT_API_PROVIDER (read once per process)."""
    name = os.getenv("CHAT_API_PROVIDER", "reference").strip().lower()
    try:
        cls = _REGISTRY[name]
    except KeyError:
        known = ", ".join(sorted(_REGISTRY))
        raise ValueError(
            f"Unknown CHAT_API_PROVIDER '{name}' — known providers: {known}"
        ) from None
    return cls()


__all__ = ["ChatProvider", "ReferenceProvider", "get_provider"]

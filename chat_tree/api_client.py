# Copyright (c) 2026 Arvindra Sehmi
# Licensed under the MIT License — see LICENSE for details.

"""Façade over the configured chat provider (CHAT_API_PROVIDER env var).

All canvas ↔ backend traffic goes through these functions; the wire-format
specifics live in chat_tree/providers/*. The canvas itself only knows the
provider contract (providers/base.py): health, per-identity session tokens,
per-node history, round deletion, and an event stream.

To plug in your own backend see docs/PROVIDERS.md — in most cases you
implement the reference protocol server-side (mock_provider.py is a working
example) and configure:

    CHAT_API_PROVIDER=reference
    CHAT_API_BASE_URL=http://localhost:9000
"""

from collections.abc import AsyncIterator
from typing import Any

from chat_tree.providers import get_provider


def base_url() -> str:
    return get_provider().base_url


def provider_name() -> str:
    return get_provider().name


def default_user_id() -> str:
    """Fallback identity when nobody is signed in via Auth0."""
    return get_provider().default_user_id()


async def check_health() -> bool:
    return await get_provider().check_health()


async def mint_session(user_id: str | None = None) -> str:
    return await get_provider().mint_session(user_id)


async def get_history(token: str, node_id: str) -> list[dict[str, Any]]:
    return await get_provider().get_history(token, node_id)


async def delete_round(token: str, node_id: str, round_num: int) -> None:
    await get_provider().delete_round(token, node_id, round_num)


def stream_chat(
    token: str, node_id: str, message: str
) -> AsyncIterator[dict[str, Any]]:
    return get_provider().stream_chat(token, node_id, message)

# Copyright (c) 2026 Arvindra Sehmi
# Licensed under the MIT License — see LICENSE for details.

"""ReferenceProvider — the canonical minimal HTTP protocol for the canvas.

If you are integrating your own backend, the easiest route is to implement
THIS protocol server-side and just set CHAT_API_PROVIDER=reference plus
CHAT_API_BASE_URL — no Python changes in the canvas at all. mock_provider.py
is a complete FastAPI implementation you can copy from (and run to try the
canvas without any real LLM).

Protocol (all JSON; auth via X-Session-Token header):
  GET    /health                          → 200 when ready
  POST   /session {"user_id": str}        → {"session_token": str}
  GET    /history/{node_id}               → {"messages": [row, ...]}
  DELETE /history/{node_id}/{round_num}   → 200 (404 if unknown)
  POST   /chat {"node_id": str, "message": str}
         → text/event-stream of "data: {json}\n\n" event dicts,
           ending with {"type": "done", "data": {"round": int}}

Event and history-row shapes: see providers/base.py.
"""

import json
import os
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

from chat_tree.providers.base import ChatProvider


class ReferenceProvider(ChatProvider):
    name = "reference"

    def __init__(self) -> None:
        self._base_url = os.getenv("CHAT_API_BASE_URL", "http://localhost:9000")

    @property
    def base_url(self) -> str:
        return self._base_url

    async def check_health(self) -> bool:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{self._base_url}/health", timeout=5)
                return resp.status_code == 200
        except Exception:
            return False

    async def mint_session(self, user_id: Optional[str] = None) -> str:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self._base_url}/session",
                json={"user_id": user_id or self.default_user_id()},
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json()["session_token"]

    async def get_history(self, token: str, node_id: str) -> List[Dict[str, Any]]:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self._base_url}/history/{node_id}",
                headers={"X-Session-Token": token},
                timeout=15,
            )
            resp.raise_for_status()
            return resp.json().get("messages", [])

    async def delete_round(self, token: str, node_id: str, round_num: int) -> None:
        async with httpx.AsyncClient() as client:
            resp = await client.delete(
                f"{self._base_url}/history/{node_id}/{round_num}",
                headers={"X-Session-Token": token},
                timeout=15,
            )
            resp.raise_for_status()

    async def stream_chat(
        self, token: str, node_id: str, message: str
    ) -> AsyncIterator[Dict[str, Any]]:
        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST",
                f"{self._base_url}/chat",
                json={"node_id": node_id, "message": message},
                headers={"X-Session-Token": token},
                timeout=httpx.Timeout(300, connect=10),
            ) as resp:
                if resp.status_code != 200:
                    body = (await resp.aread()).decode(errors="replace")[:300]
                    yield {
                        "type": "error",
                        "data": {"message": f"HTTP {resp.status_code}: {body}"},
                    }
                    return
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        try:
                            yield json.loads(line[6:])
                        except json.JSONDecodeError:
                            pass

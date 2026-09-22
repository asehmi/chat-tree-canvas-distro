# Copyright (c) 2026 Arvindra Sehmi
# Licensed under the MIT License — see LICENSE for details.

"""ChatProvider — the contract between the chat-tree canvas and any backend.

The canvas is backend-agnostic: all traffic flows through the api_client
façade, which delegates to the provider selected by CHAT_API_PROVIDER. To
plug in your own backend, implement this interface (see docs/PROVIDERS.md
for the full guide, and providers/reference.py + mock_provider.py for a
working minimal pair).

Semantics the canvas relies on
------------------------------
- node_id keys ONE prompt/answer exchange (the canvas calls each tree vertex
  a node; conversation context travels inside the message text).
- One round per node in normal use; the actual round number arrives in the
  `done` stream event and is passed back to delete_round on rerun.
- get_history is used to re-hydrate node content after reload/import; a node
  whose history is gone (404 / empty / user-turn-only) is shown prompt-only
  and the user is warned to rerun it.

Stream event dicts ({"type": ..., "data": {...}})
-------------------------------------------------
The canvas folds these into typed display blocks (packages/chat-tree-canvas-react/src/renderers/):
  text_delta        {"delta": str}                     → markdown text
  route_decision    {"mode": str}                      → header intent badge
  tool_call         {"tool": str}                      → tool status line
  tool_result       {"tool": str, "denied": bool, "reason": str}
  sandbox_start     {"code": str}                      → collapsible script
  tool_call_result  {"kind": "markdown", "text": str}
                    {"kind": "metric", "label": str, "value": Any, "unit": str}
                    {"kind": "table", "title": str, "records": [dict]}
                    {"kind": "chart", "title": str, "figure_json": str}  # Plotly
                    {"kind": "mermaid", "title": str, "code": str}       # mermaid source, rendered client-side
                    {"kind": "question", "text": str}                   # pauses the turn for user input
  result            {"final_report": str, "charts": [{"type": str, "json": str}]}
  error             {"message": str}                   → node error box
  done              {"round": int}                     → ends the exchange

Minimum viable stream: text_delta* then done.

History message rows (get_history)
----------------------------------
  {"role": "user",      "text": str}
  {"role": "assistant", "text": str, "events": [event, ...], "result": {...},
   "mode": str, "round": int, "interrupted": bool}

The canvas rebuilds blocks by replaying `events` (and `result`); when both
are absent it falls back to `text`. Minimum viable assistant row:
  {"role": "assistant", "text": str, "round": 0}
"""

import os
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Dict, List, Optional


class ChatProvider(ABC):
    """Async interface a chat backend must implement."""

    #: registry key; also the CHAT_API_PROVIDER value that selects it
    name: str = ""

    @property
    @abstractmethod
    def base_url(self) -> str:
        """Backend base URL (shown in the UI status tooltip and errors)."""

    def default_user_id(self) -> str:
        """Fallback identity when nobody is signed in via Auth0."""
        return os.getenv("CHAT_API_USER_ID", "anonymous")

    @abstractmethod
    async def check_health(self) -> bool:
        """True when the backend is reachable and ready."""

    @abstractmethod
    async def mint_session(self, user_id: Optional[str] = None) -> str:
        """Return an opaque session token for user_id (or the default user).

        Tokens are per-identity: the canvas discards its token when the
        signed-in user changes. Return "" if your backend needs no auth,
        but the method must still succeed.
        """

    @abstractmethod
    async def get_history(self, token: str, node_id: str) -> List[Dict[str, Any]]:
        """Persisted message rows for one node (see module docstring)."""

    @abstractmethod
    async def delete_round(self, token: str, node_id: str, round_num: int) -> None:
        """Delete one exchange so the node can be rerun under the same id."""

    @abstractmethod
    def stream_chat(
        self, token: str, node_id: str, message: str
    ) -> AsyncIterator[Dict[str, Any]]:
        """POST the message and yield parsed stream event dicts."""

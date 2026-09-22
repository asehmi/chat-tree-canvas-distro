# Copyright (c) 2026 Arvindra Sehmi
# Licensed under the MIT License — see LICENSE for details.

"""Canvas state — the tree of chat nodes, and streaming against the
configured chat backend (see chat_tree/providers/ and docs/PROVIDERS.md).

Terminology: node_id keys one prompt/answer exchange. A *conversation* in
this app means a full root-to-leaf path through the tree; see
chat_tree/exporter.py for the export format built on that definition.

Model (per user decision): every node is one backend exchange (node_id)
holding exactly one round (round 0 in the normal flow; the actual number is
captured from the stream's `done` event as a safety net). The context a node
sees is carried *inside the message*, built by chat_tree.context: the
prompt is sent first (so the backend's intent classifier reads it
unobscured), followed — not preceded — by its ancestor chain's
markdown/plain-text answers, latest ancestor first, behind a
"HISTORICAL CONTEXT" delimiter. Deliberately not a user/assistant turn
format; the prompt always leads.

- Clear/rerun (leaf button for now): delete the node's round server-side and
  automatically rerun the same prompt in the same context with the same node_id.
  The rerun cascades to all descendant nodes in breadth-first order (parents
  re-answer before their children, so each child streams against the fresh
  ancestor context); a failed node prunes its own branch but siblings continue.
- Duplicate (leaf): copy the prompt into a fresh node (fresh node_id) under the
  same parent — run the prompt against a possibly different context.

The tree shape (node_ids, positions, prompts, edges) persists to SQLite per
(user_id, session_id); content re-hydrates from GET /chat/history/{node_id}.

Responses are stored as typed blocks (mirroring the backend's SSE event
shapes) rather than one flattened markdown string:
  {"kind": "text",   "text": str}
  {"kind": "tool",   "tool": str, "status": "called|ok|denied", "reason": str}
  {"kind": "script", "code": str}
  {"kind": "metric", "label": str, "value": Any, "unit": str}
  {"kind": "table",  "title": str, "records": [dict]}
  {"kind": "chart",  "title": str, "figure": str}   # Plotly figure JSON
"""

import copy
import json
import logging
import time
from typing import Any, Dict, List, Optional

import httpx
import reflex as rx

from chat_tree import api_client, dag_store, exporter, storage, tree
from chat_tree.blocks import (
    apply_event_to_blocks,
    blocks_from_history,
    compose_followup_prompt,
    missing_content_warning,
)
from chat_tree.config import CASCADING_RERUNS
from chat_tree.context import ConversationContext
from chat_tree.models import CHILD_Y_GAP, SIBLING_X_GAP, make_edge, make_node
from chat_tree.utils import finite

logger = logging.getLogger(__name__)
# .db is excluded from Reflex's dev reload watcher (dot-prefixed) — see
# chat_tree/dag_store.py. Never log into a watched directory.
_LOG_DIR = __import__("pathlib").Path(__file__).resolve().parent.parent.parent / ".db"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_debug_handler = logging.FileHandler(_LOG_DIR / "debug.log", delay=True)
_debug_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
logger.addHandler(_debug_handler)
logger.setLevel(logging.INFO)

# The stream-event reducer, tree algorithms, and policy flags live in the
# shared chat_tree package (framework-free, reused by app_st).


class CanvasState(rx.State):
    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []
    session_token: str = ""
    backend_ok: bool = False
    loaded: bool = False

    user_id: str = ""
    session_id: str = ""

    # Load-session dialog (sessions = picker rows, all values pre-formatted
    # strings; "current" is "1" for the active session, "" otherwise)
    load_dialog_open: bool = False
    load_sid_value: str = ""
    load_error: str = ""
    sessions: List[Dict[str, str]] = []

    # Cascading-rerun progress ("Rerunning X of Y…"); total 0 = no rerun running
    rerun_done: int = 0
    rerun_total: int = 0

    # Import-session dialog
    import_dialog_open: bool = False

    # Clear-node confirmation: the node id pending confirmation ("" = closed)
    clear_target: str = ""
    # Rerun-scope checkboxes on the clear/rerun dialog (both default on —
    # reproduces the old, unconditional behavior when left unchanged).
    clear_cascade: bool = True
    clear_include_ancestors: bool = True
    # Delete-node confirmation: the node id pending confirmation ("" = closed)
    delete_target: str = ""

    @rx.var
    def session_short(self) -> str:
        return self.session_id[:8] if self.session_id else "—"

    @rx.var
    def delete_message(self) -> str:
        if not self.delete_target:
            return ""
        descendants = len(self._subtree(self.delete_target)) - 1
        if descendants > 0:
            return (
                f"This deletes the node and its {descendants} descendant"
                f"{'s' if descendants != 1 else ''} from the canvas. "
                "This cannot be undone."
            )
        return "This deletes the node from the canvas. This cannot be undone."

    @rx.var
    def clear_scope_message(self) -> str:
        if self.clear_cascade and self.clear_include_ancestors:
            return (
                "The node and all its descendants will be rerun "
                "(breadth-first), each against its refreshed ancestor context."
            )
        if self.clear_cascade and not self.clear_include_ancestors:
            return (
                "The node and all its descendants will be rerun "
                "(breadth-first), each using only its own prompt as written "
                "(no ancestor context)."
            )
        if not self.clear_cascade and self.clear_include_ancestors:
            return (
                "Only this node will be rerun, using its ancestor context. "
                "Descendant nodes are left unchanged."
            )
        return (
            "Only this node will be rerun, using its prompt as written "
            "(no ancestor context). Descendant nodes are left unchanged."
        )

    # ── lifecycle ─────────────────────────────────────────────

    async def _identity(self) -> str:
        """Auth0 identity when signed in, else the DEV fallback user."""
        from app_rx.states.auth_state import AuthState

        auth = await self.get_state(AuthState)
        if auth.is_authenticated and auth.user and auth.user.email:
            return auth.user.email
        return api_client.default_user_id()

    @rx.event
    async def on_load(self):
        desired = await self._identity()
        logger.info(
            "on_load inst=%s loaded=%s user=%s", id(self), self.loaded, desired
        )
        missing = 0
        if not self.loaded or desired != self.user_id:
            dag_store.init_db()
            if desired != self.user_id:
                # Bridge session tokens are per-identity.
                self.session_token = ""
            self.user_id = desired
            sid = dag_store.latest_session(self.user_id)
            if sid is None:
                self.session_id = dag_store.create_session(self.user_id)
                self.nodes = []
                self.edges = []
                self._import_legacy_canvas()
            else:
                self.session_id = sid
                missing = await self._hydrate_session(sid)
            self.loaded = True
        self.backend_ok = await api_client.check_health()
        if missing:
            return rx.toast.warning(missing_content_warning(missing))

    def _import_legacy_canvas(self) -> None:
        """One-time import of the pre-SQLite data/canvas.json."""
        nodes, edges = storage.load_legacy_canvas()
        if nodes:
            self.nodes = nodes
            self.edges = edges
            self._persist()
            storage.mark_legacy_imported()

    async def _ensure_token(self) -> str:
        if not self.session_token:
            self.session_token = await api_client.mint_session(self.user_id)
        return self.session_token

    async def _get_history(self, node_id: str) -> List[Dict[str, Any]]:
        """Fetch a node's history, re-minting the session token once on 401.

        The reference backends' session stores are in-memory, so restarting
        one invalidates the cached token — re-mint and retry rather than
        failing every node with a dead-session error. A 404 (history
        deleted) is left to propagate to the caller, which handles it
        distinctly.
        """
        try:
            return await api_client.get_history(self.session_token, node_id)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 401:
                raise
            self.session_token = await api_client.mint_session(self.user_id)
            return await api_client.get_history(self.session_token, node_id)

    async def _hydrate_session(self, sid: str) -> int:
        """Rebuild the canvas from DB shape + per-node backend history.

        Returns the number of once-submitted nodes that came back without
        content (history deleted/absent server-side). Those nodes keep their
        prompt and stay resendable (idle); callers surface the count as a
        warning so the user knows a rerun is needed.
        """
        node_rows, edge_rows = dag_store.load_dag(sid)
        nodes = []
        for row in node_rows:
            node = make_node(row["x"], row["y"], node_id=row["node_id"], prompt=row["prompt"])
            node["data"]["width"] = row.get("w", 0) or 0
            node["data"]["height"] = row.get("h", 0) or 0
            nodes.append(node)
        self.nodes = nodes
        self.edges = [
            {"id": r["edge_id"], "source": r["source"], "target": r["target"]}
            for r in edge_rows
        ]
        self._refresh_leaves()

        try:
            token = await self._ensure_token()
        except Exception:
            for node in self.nodes:
                if node["data"]["prompt"]:
                    node["data"]["status"] = "error"
                    node["data"]["error"] = (
                        f"History unavailable — backend unreachable at {api_client.base_url()}."
                    )
            return 0

        missing = 0
        for node in self.nodes:
            data = node["data"]
            if not data["prompt"]:
                continue  # never-submitted node stays editable
            try:
                messages = await self._get_history(node["id"])
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    # History deleted server-side → prompt-only, resendable.
                    data["status"] = "idle"
                    missing += 1
                else:
                    data["status"] = "error"
                    data["error"] = f"History fetch failed: {exc}"
                continue
            except Exception as exc:
                data["status"] = "error"
                data["error"] = f"History fetch failed: {exc}"
                continue
            assistant = next(
                (m for m in messages if m.get("role") == "assistant"), None
            )
            if assistant is None:
                # Empty history or user turn only → prompt-only, resendable.
                data["status"] = "idle"
                missing += 1
                continue
            data["blocks"] = blocks_from_history(assistant)
            data["route"] = assistant.get("mode") or ""
            data["round"] = assistant.get("round", 0)
            data["status"] = "complete"
            if assistant.get("interrupted"):
                data["error"] = "This response was interrupted mid-stream."
        return missing

    # ── persistence & derived flags ───────────────────────────

    def _refresh_leaves(self) -> None:
        tree.refresh_leaves(self.nodes, self.edges)

    def _persist(self) -> None:
        self._refresh_leaves()
        logger.info(
            "persist inst=%s sid=%s geom=%s",
            id(self),
            self.session_id[:8],
            [
                (n["id"][:8], round(n["position"]["x"]), round(n["position"]["y"]),
                 n["data"].get("width"), n["data"].get("height"))
                for n in self.nodes
            ],
        )
        try:
            dag_store.save_dag(
                self.session_id,
                self.user_id,
                [dict(n) for n in self.nodes],
                [dict(e) for e in self.edges],
            )
        except Exception:
            # A persist failure must be loud — silently losing every
            # subsequent save was how resize/move geometry went missing.
            logger.exception("persist FAILED (sid=%s)", self.session_id)
            raise

    # ── graph helpers ─────────────────────────────────────────

    def _node_index(self, node_id: str) -> Optional[int]:
        return tree.node_index(self.nodes, node_id)

    def _parent_of(self, node_id: str, exclude_edge: str | None = None) -> Optional[str]:
        return tree.parent_of(self.edges, node_id, exclude_edge)

    def _children_of(self, node_id: str) -> List[str]:
        return tree.children_of(self.edges, node_id)

    def _is_ancestor(
        self, maybe_ancestor: str, node_id: str, exclude_edge: str | None = None
    ) -> bool:
        return tree.is_ancestor(self.edges, maybe_ancestor, node_id, exclude_edge)

    def _build_message(
        self, node_id: str, prompt: str, include_ancestors: bool
    ) -> str:
        """The full message to send: prompt first, then — if
        include_ancestors — ancestor answers postfixed latest-first. See
        chat_tree.context.ConversationContext."""
        return ConversationContext.build(
            self.nodes, self.edges, node_id, prompt, include_ancestors
        ).to_prompt()

    def _validate_link(
        self, source: str, target: str, exclude_edge: str | None = None
    ) -> str:
        """'' if source→target is a legal tree edge, else a reason."""
        return tree.validate_link(self.nodes, self.edges, source, target, exclude_edge)


    @rx.event
    def add_root(self, x: float, y: float):
        self.nodes.append(make_node(x, y))
        self._persist()

    @rx.event
    def add_root_auto(self):
        """Toolbar button: place a new root right of the current graph."""
        if self.nodes:
            max_x = max(n["position"]["x"] for n in self.nodes)
            min_y = min(n["position"]["y"] for n in self.nodes)
            self.nodes.append(make_node(max_x + SIBLING_X_GAP, min_y))
        else:
            self.nodes.append(make_node(0, 0))
        self._persist()

    def _add_child(self, node_id: str, prompt: str = "") -> Optional[Dict[str, Any]]:
        """Append a child node under node_id, optionally with a prefilled
        (still editable — status stays idle) prompt."""
        idx = self._node_index(node_id)
        if idx is None:
            return None
        parent = self.nodes[idx]
        siblings = len(self._children_of(node_id))
        child = make_node(
            parent["position"]["x"] + siblings * SIBLING_X_GAP,
            parent["position"]["y"] + CHILD_Y_GAP,
            prompt=prompt,
        )
        self.nodes.append(child)
        self.edges.append(make_edge(node_id, child["id"]))
        self._persist()
        return child

    @rx.event
    def branch_node(self, node_id: str):
        self._add_child(node_id)

    @rx.event
    def branch_node_from_text(self, node_id: str, text: str):
        """Branch from a text selection inside a response: the child node's
        prompt is prefilled quoting the selected phrase (LMCanvas-style)."""
        text = " ".join((text or "").split())
        if len(text) > 280:
            text = text[:280].rstrip() + "…"
        prompt = f'Regarding "{text}": ' if text else ""
        self._add_child(node_id, prompt=prompt)

    @rx.event(background=True)
    async def answer_question(self, node_id: str, question_text: str, answer: str):
        """A `question` block's answer becomes a new child node — auto-
        submitted with the parent's full ancestor context — rather than a
        rerun of the asking node. The child's prompt is frozen at creation
        time; it's also what ChatCanvas.jsx matches against to render the
        parent's question as resolved instead of interactive."""
        async with self:
            child = self._add_child(
                node_id, prompt=compose_followup_prompt(question_text, answer)
            )
            if child is None:
                return
            child_id = child["id"]
        await self._run_node(child_id)

    @rx.event
    def duplicate_node(self, node_id: str):
        """Leaf only: copy the prompt into a fresh node (fresh node_id) under the
        same parent; response stays empty so Send is available."""
        idx = self._node_index(node_id)
        if idx is None:
            return
        # Leaf-only gate, controlled by CASCADING_RERUNS (see module top).
        if not CASCADING_RERUNS and self._children_of(node_id):
            return rx.toast.error("Only leaf nodes can be duplicated.")
        original = self.nodes[idx]
        dup = make_node(
            original["position"]["x"] + SIBLING_X_GAP,
            original["position"]["y"],
            prompt=original["data"].get("prompt", ""),
        )
        self.nodes.append(dup)
        parent = self._parent_of(node_id)
        if parent is not None:
            self.edges.append(make_edge(parent, dup["id"]))
        self._persist()

    def _subtree(self, node_id: str) -> set:
        return tree.subtree(self.edges, node_id)


    @rx.event
    def request_delete(self, node_id: str):
        if self._node_index(node_id) is not None:
            self.delete_target = node_id

    @rx.event
    def cancel_delete(self):
        self.delete_target = ""

    @rx.event
    def confirm_delete(self):
        node_id = self.delete_target
        self.delete_target = ""
        if self._node_index(node_id) is None:
            return
        doomed = self._subtree(node_id)
        self.nodes = [n for n in self.nodes if n["id"] not in doomed]
        self.edges = [
            e
            for e in self.edges
            if e["source"] not in doomed and e["target"] not in doomed
        ]
        self._persist()

    @rx.event
    def node_moved(self, node_id: str, x: float, y: float):
        logger.info("node_moved %s x=%s y=%s", node_id[:8], x, y)
        idx = self._node_index(node_id)
        if idx is not None:
            self.nodes[idx]["position"] = {
                "x": finite(x, self.nodes[idx]["position"]["x"]),
                "y": finite(y, self.nodes[idx]["position"]["y"]),
            }
            self._persist()

    @rx.event
    def node_resized(self, node_id: str, x: float, y: float, width: float, height: float):
        logger.info(
            "node_resized %s x=%s y=%s w=%s h=%s", node_id[:8], x, y, width, height
        )
        idx = self._node_index(node_id)
        if idx is not None:
            node = self.nodes[idx]
            node["position"] = {
                "x": finite(x, node["position"]["x"]),
                "y": finite(y, node["position"]["y"]),
            }
            node["data"]["width"] = finite(width)
            node["data"]["height"] = finite(height)
            self._persist()

    # ── manual edge wiring ────────────────────────────────────

    @rx.event
    def connect_edge(self, source: str, target: str):
        """User dragged a new connection between two handles."""
        reason = self._validate_link(source, target)
        if reason:
            return rx.toast.error(reason)
        self.edges.append(make_edge(source, target))
        self._persist()

    @rx.event
    def reconnect_edge(self, edge_id: str, source: str, target: str):
        """User dragged an existing edge end onto another node."""
        for i, edge in enumerate(self.edges):
            if edge["id"] == edge_id:
                reason = self._validate_link(source, target, exclude_edge=edge_id)
                if reason:
                    # Force a state push so the client snaps back to the
                    # unchanged edge list.
                    self.edges = list(self.edges)
                    return rx.toast.error(reason)
                self.edges[i] = make_edge(source, target)
                self.edges = list(self.edges)
                self._persist()
                return
        return rx.toast.error("Edge no longer exists.")

    @rx.event
    def delete_edge(self, edge_id: str):
        """User dropped an edge end on empty canvas → detach (child becomes a root)."""
        before = len(self.edges)
        self.edges = [e for e in self.edges if e["id"] != edge_id]
        if len(self.edges) != before:
            self._persist()

    # ── clear/rerun: drop the round server-side, rerun node + descendants ──

    @rx.event
    def request_clear(self, node_id: str):
        idx = self._node_index(node_id)
        if idx is None:
            return
        # Leaf-only gate, controlled by CASCADING_RERUNS (see module top);
        # mirrors the button visibility gate in chat_canvas.jsx.
        if not CASCADING_RERUNS and self._children_of(node_id):
            return rx.toast.error("Only leaf nodes can be cleared.")
        self.clear_target = node_id
        # Reset to the default scope (both on) each time the dialog opens.
        self.clear_cascade = True
        self.clear_include_ancestors = True

    @rx.event
    def cancel_clear(self):
        self.clear_target = ""

    @rx.event
    def set_clear_cascade(self, value: bool):
        self.clear_cascade = value

    @rx.event
    def set_clear_include_ancestors(self, value: bool):
        self.clear_include_ancestors = value

    @rx.event
    def confirm_clear(self):
        node_id = self.clear_target
        cascade = self.clear_cascade
        include_ancestors = self.clear_include_ancestors
        self.clear_target = ""
        if self._node_index(node_id) is None:
            return
        return CanvasState.rerun_subtree(node_id, cascade, include_ancestors)

    # ── sessions ──────────────────────────────────────────────

    @rx.event
    def new_session(self):
        self.session_id = dag_store.create_session(self.user_id)
        self.nodes = []
        self.edges = []
        self._persist()

    @rx.event
    def open_load_dialog(self):
        self.load_sid_value = ""
        self.load_error = ""
        rows = dag_store.list_sessions(self.user_id)
        sessions: List[Dict[str, str]] = []
        for row in rows:
            title = " ".join((row["first_prompt"] or "").split())
            if len(title) > 60:
                title = title[:60].rstrip() + "…"
            sessions.append(
                {
                    "sid": row["session_id"],
                    "short": row["session_id"][:8],
                    "when": time.strftime(
                        "%Y-%m-%d %H:%M", time.localtime(row["updated_at"])
                    ),
                    "nodes": str(row["node_count"]),
                    "title": title or "(empty)",
                    "current": "1" if row["session_id"] == self.session_id else "",
                }
            )
        self.sessions = sessions
        self.load_dialog_open = True

    @rx.event
    def close_load_dialog(self):
        self.load_dialog_open = False

    @rx.event
    def set_load_sid_value(self, value: str):
        self.load_sid_value = value

    async def _load_session(self, sid: str) -> tuple[str, int]:
        """Load a session by id. Returns (error_message, missing_count) —
        error '' on success; missing_count = nodes without backend content."""
        sid = (sid or "").strip()
        if not sid:
            return "Enter a session ID.", 0
        owner = dag_store.session_owner(sid)
        if owner is None or owner != self.user_id:
            # Same policy as the backend: don't reveal foreign sessions.
            return "No such session.", 0
        self.session_id = sid
        missing = await self._hydrate_session(sid)
        return "", missing

    def _loaded_session_events(self, missing: int) -> list:
        events = [rx.toast.success(f"Loaded session {self.session_id[:8]}…")]
        if missing:
            events.append(rx.toast.warning(missing_content_warning(missing)))
        return events

    @rx.event
    async def load_session_confirm(self):
        """Load from the manually-pasted session ID."""
        error, missing = await self._load_session(self.load_sid_value)
        if error:
            self.load_error = error
            return
        self.load_dialog_open = False
        return self._loaded_session_events(missing)

    @rx.event
    def load_session_pick(self, sid: str):
        """Picker row clicked: select it — the id lands in the text field as
        visible feedback (and the row highlights). OK performs the load."""
        self.load_sid_value = sid
        self.load_error = ""

    # ── auto-layout ───────────────────────────────────────────

    @rx.event
    def tidy_tree(self):
        """Tidy Tree: reposition every node — the layout math lives in
        chat_tree.tree.tidy_layout (one row per depth, children centered
        under parents, anchored so the viewport doesn't jump)."""
        positions = tree.tidy_layout(list(self.nodes), list(self.edges))
        if not positions:
            return
        for node in self.nodes:
            pos = positions.get(node["id"])
            if pos is not None:  # unreachable under corrupt edges — leave in place
                node["position"] = {"x": pos[0], "y": pos[1]}
        self._persist()

    # ── export / import (shared intermediate format, exporter.py) ──

    @rx.event(background=True)
    async def export_session(self):
        """Download the session as a chat-tree-canvas/v1 JSON file: the exact
        canvas shape plus coalesced root→leaf conversations built from the
        backend history of every node (fetched here, one call per node)."""
        async with self:
            sid = self.session_id
            uid = self.user_id
            nodes = copy.deepcopy([dict(n) for n in self.nodes])
            edges = [dict(e) for e in self.edges]
            token = self.session_token

        if not token:
            try:
                token = await api_client.mint_session(uid)
                async with self:
                    self.session_token = token
            except Exception:  # noqa: BLE001 — export still works, sans answers
                token = ""

        histories: Dict[str, Any] = {}
        fetch_failures = 0
        for node in nodes:
            if not (node["data"].get("prompt") or "").strip():
                continue  # never submitted — no history to fetch
            if token:
                try:
                    histories[node["id"]] = await api_client.get_history(
                        token, node["id"]
                    )
                    continue
                except Exception:  # noqa: BLE001
                    pass
            histories[node["id"]] = None
            fetch_failures += 1

        doc = exporter.build_export(sid, uid, nodes, edges, histories)
        data = json.dumps(doc, indent=2, ensure_ascii=False)
        if fetch_failures:
            yield rx.toast.warning(
                f"Exported, but history for {fetch_failures} node(s) was "
                "unavailable — their conversations are marked partial."
            )
        yield rx.download(data=data, filename=f"chat-tree-{sid[:8]}.json")

    @rx.event
    def open_import_dialog(self):
        self.import_dialog_open = True

    @rx.event
    def close_import_dialog(self):
        self.import_dialog_open = False

    @rx.event
    async def import_session_upload(self, files: list[rx.UploadFile]):
        """Import a v1 export file as a NEW session.

        node_ids are kept verbatim: they are the backend history keys, so on
        the same backend the imported nodes re-hydrate their responses in
        full. (Two sessions may then share node_ids — clearing/rerunning a
        node in one affects the other's history, same as loading any old
        session twice.)
        """
        if not files:
            return rx.toast.error("Choose a JSON export file first.")
        raw = await files[0].read()
        try:
            canvas = exporter.parse_import(raw)
        except ValueError as exc:
            return rx.toast.error(str(exc))

        self.session_id = dag_store.create_session(self.user_id)
        nodes = []
        for row in canvas["nodes"]:
            node = make_node(
                finite(row.get("x", 0)),
                finite(row.get("y", 0)),
                node_id=row["node_id"],
                prompt=row.get("prompt", ""),
            )
            node["data"]["width"] = finite(row.get("w", 0))
            node["data"]["height"] = finite(row.get("h", 0))
            nodes.append(node)
        self.nodes = nodes
        self.edges = [
            {"id": e["edge_id"], "source": e["source"], "target": e["target"]}
            for e in canvas["edges"]
        ]
        self._persist()
        missing = await self._hydrate_session(self.session_id)
        self.import_dialog_open = False
        events = [
            rx.toast.success(
                f"Imported {len(nodes)} node(s) into session {self.session_id[:8]}…"
            )
        ]
        if missing:
            events.append(rx.toast.warning(missing_content_warning(missing)))
        return events

    # ── chat streaming ────────────────────────────────────────

    @rx.event(background=True)
    async def submit_prompt(self, node_id: str, prompt: str):
        prompt = (prompt or "").strip()
        if not prompt:
            return

        async with self:
            idx = self._node_index(node_id)
            if idx is None:
                return
            self.nodes[idx]["data"]["prompt"] = prompt
            self._persist()  # prompt is part of the DAG shape

        await self._run_node(node_id)

    async def _run_node(self, node_id: str, include_ancestors: bool = True) -> bool:
        """Stream one node's stored prompt against the backend.

        Shared core of submit_prompt and rerun_subtree; must be called from a
        background event (it takes `async with self` locks itself). Returns
        True when the node finished with an error-free answer, False when it
        vanished, had no prompt, or ended in error.
        """
        async with self:
            idx = self._node_index(node_id)
            if idx is None:
                return False
            data = self.nodes[idx]["data"]
            prompt = (data.get("prompt") or "").strip()
            if not prompt:
                return False
            data["status"] = "streaming"
            data["blocks"] = []
            data["error"] = ""
            data["route"] = ""
            message = self._build_message(node_id, prompt, include_ancestors)
            token = self.session_token
            uid = self.user_id

        blocks: List[Dict[str, Any]] = []
        route = ""
        error = ""
        round_num = 0

        async def push(final: bool = False) -> bool:
            """Write accumulated blocks into the node; False if node is gone."""
            async with self:
                i = self._node_index(node_id)
                if i is None:
                    return False
                node_data = self.nodes[i]["data"]
                node_data["blocks"] = copy.deepcopy(blocks)
                node_data["route"] = route
                if final:
                    node_data["error"] = error
                    node_data["round"] = round_num
                    node_data["status"] = (
                        "error" if (error and not blocks) else "complete"
                    )
                    self.backend_ok = not error.startswith("Cannot reach")
                    self._persist()
            return True

        try:
            # Retry once: the backend session store is in-memory, so a backend
            # restart makes the cached token 401 — re-mint and restart the stream.
            for _attempt in range(2):
                if not token:
                    token = await api_client.mint_session(uid)
                    async with self:
                        self.session_token = token

                retry_401 = False
                blocks.clear()
                route = ""
                error = ""
                last_push = 0.0
                async for event in api_client.stream_chat(token, node_id, message):
                    etype = event.get("type")
                    if etype == "route_decision":
                        route = (event.get("data") or {}).get("mode", "")
                    elif etype == "error":
                        msg = (event.get("data") or {}).get("message", "Unknown backend error.")
                        # A 401 before any content = dead cached token: re-mint & restart.
                        if _attempt == 0 and not blocks and msg.startswith("HTTP 401"):
                            retry_401 = True
                            token = ""
                            break
                        error = msg
                    elif etype == "done":
                        round_from_done = (event.get("data") or {}).get("round")
                        if round_from_done is not None:
                            round_num = round_from_done
                    else:
                        apply_event_to_blocks(blocks, event)

                    now = time.monotonic()
                    if etype != "text_delta" or now - last_push > 0.08:
                        last_push = now
                        if not await push():
                            return False  # node deleted mid-stream

                if not retry_401:
                    break

        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            error = f"Cannot reach backend at {api_client.base_url()}: {exc}"
        except Exception as exc:  # noqa: BLE001 — surface anything on the node
            error = str(exc)

        if not await push(final=True):
            return False
        return not error

    # ── rerun: node + all descendants, breadth-first ──────────

    def _bfs_order(self, node_id: str) -> List[str]:
        return tree.bfs_order(self.edges, node_id)


    @rx.event(background=True)
    async def rerun_subtree(
        self, node_id: str, cascade: bool = True, include_ancestors: bool = True
    ):
        """Clear and rerun a node, optionally cascading to its descendants.

        `cascade` selects the run order: the full subtree (breadth-first) when
        True, or just `node_id` alone when False. `include_ancestors` controls
        whether each rerun prompt is postfixed with its ancestor chain's
        answers (as normal, see chat_tree.context) or sent as written, with
        no history — this applies uniformly to every node processed, not
        just `node_id`.

        BFS guarantees every ancestor re-answers before its descendants, so
        (when include_ancestors is True) each descendant's ancestor context is
        built from the *fresh* answers. Nodes run sequentially. A node that
        fails (or is streaming, or was deleted mid-run) prunes its own
        branch; sibling branches continue. For a leaf node, or when cascade
        is False, this degenerates to: clear the round, rerun in place.
        """
        async with self:
            if self._node_index(node_id) is None:
                return
            order = self._bfs_order(node_id) if cascade else [node_id]
            self.rerun_done = 0
            self.rerun_total = len(order)

        failed: set = set()
        try:
            for nid in order:
                async with self:
                    self.rerun_done += 1
                    idx = self._node_index(nid)
                    parent = self._parent_of(nid)
                    if idx is None:
                        data = None
                    else:
                        data = self.nodes[idx]["data"]
                        status = data.get("status")
                        round_num = data.get("round") or 0
                        has_prompt = bool((data.get("prompt") or "").strip())

                if data is None or (parent in failed):
                    failed.add(nid)
                    continue
                if status == "streaming":
                    # Never clobber an in-flight stream — skip its branch too.
                    failed.add(nid)
                    continue
                if not has_prompt:
                    continue  # never-submitted node: nothing to rerun

                # Drop the old exchange server-side so the node_id holds only the
                # rerun (the node model assumes one round per conversation).
                if status in ("complete", "error"):
                    async with self:
                        token = self.session_token
                        uid = self.user_id
                    try:
                        if not token:
                            token = await api_client.mint_session(uid)
                            async with self:
                                self.session_token = token
                        await api_client.delete_round(token, nid, round_num)
                    except Exception as exc:  # noqa: BLE001
                        # A complete node definitely holds a server round —
                        # losing the delete would leave two rounds on the node_id,
                        # so stop this branch. An error node may hold nothing
                        # (the delete 404s); rerun it anyway.
                        if status == "complete":
                            async with self:
                                i2 = self._node_index(nid)
                                if i2 is not None:
                                    self.nodes[i2]["data"]["error"] = f"Clear failed: {exc}"
                            failed.add(nid)
                            continue

                if not await self._run_node(nid, include_ancestors=include_ancestors):
                    failed.add(nid)
        finally:
            async with self:
                self.rerun_done = 0
                self.rerun_total = 0

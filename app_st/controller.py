# Copyright (c) 2026 Arvindra Sehmi
# Licensed under the MIT License — see LICENSE for details.

"""Chat-tree wiring for the Streamlit host — the synchronous,
st.session_state-based counterpart to app_rx/states/canvas_state.py.

Every user-visible behavior (branching, cascading reruns, tidy layout,
export/import, session picker, hydration) delegates to the SAME chat_tree
package app_rx uses; nothing here reimplements tree logic — it only adapts
the *orchestration* to Streamlit's synchronous, single-threaded,
script-reruns-on-every-interaction model instead of Reflex's async State +
background events.

Sessions are stored in the same .db/canvas.db as app_rx — loading the same
session_id in either host shows the same tree.

Streaming model (submit_prompt AND reruns, single-node or a full cascade —
see start_rerun/_advance_cascade): a background thread owns ONE asyncio
event loop for the whole SSE stream's lifetime and writes into a plain
`_StreamBox` (never `st.session_state` directly — see start_stream's
docstring). A st.fragment(run_every=...) in app.py ticks on the script
thread, copies the box's contents into SS.nodes under lock, and pushes a
`patch={"node_id": ..., "data": ...}` update through chat_tree_canvas_st —
one call per tick, since a *keyed* v2 component can only be updated once
per script run (confirmed the hard way: calling it twice in the same run,
even via the same st.empty() slot, raises StreamlitDuplicateElementKey —
Streamlit computes a keyed component's registration id from the key alone,
not its data, so identical keys collide regardless of payload). A cascade
chains from one node to the next, in strict parent-before-child order,
within tick_stream once each node's box is done — no second thread/fragment
needed, since only one node streams at a time by construction. See
_pm/LIVE_TOKEN_STREAMING_IN_APP_ST.md for the full design history,
including the wrong turns before this one.
"""

from __future__ import annotations

import asyncio
import copy
import json
import threading
import time as _time
from typing import Any, Dict, List, Optional, Tuple

import httpx
import streamlit as st

from chat_tree import api_client, dag_store, exporter, tree
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
from chat_tree_canvas_st import chat_tree_canvas

SS = st.session_state


def _run(coro):
    """Bridge chat_tree's async provider calls into Streamlit's sync script
    execution. Streamlit runs each session's script on its own worker thread
    with no ambient event loop in that thread, so asyncio.run() is safe to
    call repeatedly within one script run (same pattern any sync caller of
    an async library uses; httpx's own sync client works the same way)."""
    return asyncio.run(coro)


# ── lifecycle ────────────────────────────────────────────────

def ensure_loaded(user_id: str) -> None:
    """Once per Streamlit session: init the DB, resolve the session, hydrate.

    Unlike CanvasState._identity (which handles an Auth0 identity CHANGING
    mid-session — one long-lived Reflex connection can outlive a login),
    Streamlit tears down and rebuilds session_state on every st.login /
    st.logout navigation, so there's no "identity changed under us" case to
    handle here — identity is fixed for the life of this script's session.
    """
    if SS.get("loaded"):
        return
    dag_store.init_db()
    SS.user_id = user_id
    SS.session_token = ""
    sid = dag_store.latest_session(user_id)
    missing = 0
    if sid is None:
        SS.session_id = dag_store.create_session(user_id)
        SS.nodes = []
        SS.edges = []
    else:
        SS.session_id = sid
        missing = _hydrate_session(sid)
    SS.loaded = True
    SS.backend_ok = _run(api_client.check_health())
    if missing:
        st.toast(missing_content_warning(missing), icon=":material/warning:")


async def _ensure_token() -> str:
    if not SS.get("session_token"):
        SS.session_token = await api_client.mint_session(SS.user_id)
    return SS.session_token


def _hydrate_session(sid: str) -> int:
    """Rebuild nodes/edges from DB shape + per-node backend history.

    Synchronous port of CanvasState._hydrate_session — see that docstring
    for the missing-content-warning contract (nodes whose backend history is
    gone hydrate as prompt-only/idle rather than erroring).
    """
    node_rows, edge_rows = dag_store.load_dag(sid)
    nodes = []
    for row in node_rows:
        node = make_node(row["x"], row["y"], node_id=row["node_id"], prompt=row["prompt"])
        node["data"]["width"] = row.get("w", 0) or 0
        node["data"]["height"] = row.get("h", 0) or 0
        nodes.append(node)
    edges = [{"id": r["edge_id"], "source": r["source"], "target": r["target"]} for r in edge_rows]
    tree.refresh_leaves(nodes, edges)
    SS.nodes = nodes
    SS.edges = edges

    async def _go() -> int:
        try:
            token = await _ensure_token()
        except Exception:
            for node in SS.nodes:
                if node["data"]["prompt"]:
                    node["data"]["status"] = "error"
                    node["data"]["error"] = (
                        f"History unavailable — backend unreachable at {api_client.base_url()}."
                    )
            return 0

        missing = 0
        for node in SS.nodes:
            data = node["data"]
            if not data["prompt"]:
                continue  # never-submitted node stays editable
            try:
                messages = await api_client.get_history(token, node["id"])
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    data["status"] = "idle"  # history deleted server-side → resendable
                    missing += 1
                else:
                    data["status"] = "error"
                    data["error"] = f"History fetch failed: {exc}"
                continue
            except Exception as exc:  # noqa: BLE001
                data["status"] = "error"
                data["error"] = f"History fetch failed: {exc}"
                continue
            assistant = next((m for m in messages if m.get("role") == "assistant"), None)
            if assistant is None:
                data["status"] = "idle"  # empty/user-turn-only history → resendable
                missing += 1
                continue
            data["blocks"] = blocks_from_history(assistant)
            data["route"] = assistant.get("mode") or ""
            data["round"] = assistant.get("round", 0)
            data["status"] = "complete"
            if assistant.get("interrupted"):
                data["error"] = "This response was interrupted mid-stream."
        return missing

    return _run(_go())


def _persist() -> None:
    tree.refresh_leaves(SS.nodes, SS.edges)
    dag_store.save_dag(
        SS.session_id, SS.user_id, [dict(n) for n in SS.nodes], [dict(e) for e in SS.edges]
    )


# ── graph mutations (thin — delegate everything to chat_tree.tree) ──

def node_index(node_id: str) -> Optional[int]:
    return tree.node_index(SS.nodes, node_id)


def add_root(x: float, y: float) -> None:
    SS.nodes.append(make_node(x, y))
    _persist()


def add_root_auto() -> None:
    """New Chat button: place a new root right of the current graph."""
    if SS.nodes:
        max_x = max(n["position"]["x"] for n in SS.nodes)
        min_y = min(n["position"]["y"] for n in SS.nodes)
        SS.nodes.append(make_node(max_x + SIBLING_X_GAP, min_y))
    else:
        SS.nodes.append(make_node(0, 0))
    _persist()


def _add_child(node_id: str, prompt: str = "") -> Optional[Dict[str, Any]]:
    idx = node_index(node_id)
    if idx is None:
        return None
    parent = SS.nodes[idx]
    siblings = len(tree.children_of(SS.edges, node_id))
    child = make_node(
        parent["position"]["x"] + siblings * SIBLING_X_GAP,
        parent["position"]["y"] + CHILD_Y_GAP,
        prompt=prompt,
    )
    SS.nodes.append(child)
    SS.edges.append(make_edge(node_id, child["id"]))
    _persist()
    return child


def branch_node(node_id: str) -> None:
    _add_child(node_id)


def branch_node_from_text(node_id: str, text: str) -> None:
    """Branch from a text selection inside a response: the child node's
    prompt is prefilled quoting the selected phrase (LMCanvas-style)."""
    text = " ".join((text or "").split())
    if len(text) > 280:
        text = text[:280].rstrip() + "…"
    prompt = f'Regarding "{text}": ' if text else ""
    _add_child(node_id, prompt=prompt)


def answer_question(node_id: str, question_text: str, answer: str) -> None:
    """A `question` block's answer becomes a new child node — auto-submitted
    with the parent's full ancestor context — rather than a rerun of the
    asking node. See _pm/LIVE_TOKEN_STREAMING_IN_APP_ST.md-adjacent design
    notes: the child's prompt is frozen at creation time and is also what
    ChatCanvas.jsx matches against to render the parent's question as
    resolved."""
    if _reject_if_streaming():
        return
    child = _add_child(node_id, prompt=compose_followup_prompt(question_text, answer))
    if child is None:
        return
    start_stream(child["id"])


def duplicate_node(node_id: str) -> Optional[str]:
    """Leaf only (unless CASCADING_RERUNS): copy the prompt into a fresh
    node under the same parent. Returns an error message to toast, else None."""
    idx = node_index(node_id)
    if idx is None:
        return None
    if not CASCADING_RERUNS and tree.children_of(SS.edges, node_id):
        return "Only leaf nodes can be duplicated."
    original = SS.nodes[idx]
    dup = make_node(
        original["position"]["x"] + SIBLING_X_GAP,
        original["position"]["y"],
        prompt=original["data"].get("prompt", ""),
    )
    SS.nodes.append(dup)
    parent = tree.parent_of(SS.edges, node_id)
    if parent is not None:
        SS.edges.append(make_edge(parent, dup["id"]))
    _persist()
    return None


def delete_message(node_id: str) -> str:
    if not node_id or node_index(node_id) is None:
        return ""
    descendants = len(tree.subtree(SS.edges, node_id)) - 1
    if descendants > 0:
        return (
            f"This deletes the node and its {descendants} descendant"
            f"{'s' if descendants != 1 else ''} from the canvas. This cannot be undone."
        )
    return "This deletes the node from the canvas. This cannot be undone."


def clear_scope_message(cascade: bool, include_ancestors: bool) -> str:
    if cascade and include_ancestors:
        return (
            "The node and all its descendants will be rerun (breadth-first), "
            "each against its refreshed ancestor context."
        )
    if cascade and not include_ancestors:
        return (
            "The node and all its descendants will be rerun (breadth-first), "
            "each using only its own prompt as written (no ancestor context)."
        )
    if not cascade and include_ancestors:
        return (
            "Only this node will be rerun, using its ancestor context. "
            "Descendant nodes are left unchanged."
        )
    return (
        "Only this node will be rerun, using its prompt as written "
        "(no ancestor context). Descendant nodes are left unchanged."
    )


def confirm_delete(node_id: str) -> None:
    doomed = tree.subtree(SS.edges, node_id)
    SS.nodes = [n for n in SS.nodes if n["id"] not in doomed]
    SS.edges = [e for e in SS.edges if e["source"] not in doomed and e["target"] not in doomed]
    _persist()


def node_moved(node_id: str, x: float, y: float) -> None:
    idx = node_index(node_id)
    if idx is not None:
        SS.nodes[idx]["position"] = {
            "x": finite(x, SS.nodes[idx]["position"]["x"]),
            "y": finite(y, SS.nodes[idx]["position"]["y"]),
        }
        _persist()


def node_resized(node_id: str, x: float, y: float, width: float, height: float) -> None:
    idx = node_index(node_id)
    if idx is not None:
        node = SS.nodes[idx]
        node["position"] = {"x": finite(x, node["position"]["x"]), "y": finite(y, node["position"]["y"])}
        node["data"]["width"] = finite(width)
        node["data"]["height"] = finite(height)
        _persist()


def connect_edge(source: str, target: str) -> Optional[str]:
    reason = tree.validate_link(SS.nodes, SS.edges, source, target)
    if reason:
        return reason
    SS.edges.append(make_edge(source, target))
    _persist()
    return None


def reconnect_edge(edge_id: str, source: str, target: str) -> Optional[str]:
    for i, edge in enumerate(SS.edges):
        if edge["id"] == edge_id:
            reason = tree.validate_link(SS.nodes, SS.edges, source, target, exclude_edge=edge_id)
            if reason:
                return reason
            SS.edges[i] = make_edge(source, target)
            _persist()
            return None
    return "Edge no longer exists."


def delete_edge(edge_id: str) -> None:
    before = len(SS.edges)
    SS.edges = [e for e in SS.edges if e["id"] != edge_id]
    if len(SS.edges) != before:
        _persist()


def tidy_tree() -> None:
    """One row per depth, children centered under parents — see
    chat_tree.tree.tidy_layout for the full geometry."""
    positions = tree.tidy_layout(list(SS.nodes), list(SS.edges))
    if not positions:
        return
    for node in SS.nodes:
        pos = positions.get(node["id"])
        if pos is not None:  # unreachable under corrupt edges — leave in place
            node["position"] = {"x": pos[0], "y": pos[1]}
    _persist()


# ── chat streaming — live ────────────────────────────────────
#
# Both a fresh submit and a rerun (single-node or a full cascade) go
# through the same start_stream/tick_stream mechanism now: a background
# thread streams one node, app.py's fragment ticks it forward, and
# _advance_cascade chains to the next node (if any) once one finishes.

class _StreamBox:
    """Shared between the streaming thread and the script thread. The thread
    only ever mutates ITS OWN attributes here — never st.session_state
    directly, since writing session_state requires a ScriptRunContext that
    only exists on the script thread. The lock guards against the script
    thread reading mid-mutation (e.g. apply_event_to_blocks isn't atomic)."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.blocks: List[Dict[str, Any]] = []
        self.route = ""
        self.error = ""
        self.round = 0
        self.done = False


def _stream_worker(box: _StreamBox, token: str, node_id: str, message: str) -> None:
    """Thread target: owns ONE event loop for the whole SSE stream's
    lifetime. Must not touch st.session_state — see _StreamBox."""

    async def _go() -> None:
        try:
            async for event in api_client.stream_chat(token, node_id, message):
                etype = event.get("type")
                with box.lock:
                    if etype == "route_decision":
                        box.route = (event.get("data") or {}).get("mode", "")
                    elif etype == "error":
                        box.error = (event.get("data") or {}).get("message", "Unknown backend error.")
                    elif etype == "done":
                        d = (event.get("data") or {}).get("round")
                        if d is not None:
                            box.round = d
                    else:
                        apply_event_to_blocks(box.blocks, event)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            with box.lock:
                box.error = f"Cannot reach backend at {api_client.base_url()}: {exc}"
        except Exception as exc:  # noqa: BLE001 — surface anything on the node
            with box.lock:
                box.error = str(exc)
        finally:
            with box.lock:
                box.done = True

    asyncio.run(_go())


def start_stream(node_id: str, include_ancestors: bool = True) -> bool:
    """Kick off a live stream for one node's stored prompt on a background
    thread; app.py's fragment (see main()) ticks it forward and pushes
    patches. Returns False if there's nothing to run."""
    idx = node_index(node_id)
    if idx is None:
        return False
    data = SS.nodes[idx]["data"]
    prompt = (data.get("prompt") or "").strip()
    if not prompt:
        return False
    data["status"] = "streaming"
    data["blocks"] = []
    data["error"] = ""
    data["route"] = ""
    message = ConversationContext.build(
        SS.nodes, SS.edges, node_id, prompt, include_ancestors
    ).to_prompt()
    token = SS.get("session_token") or _run(api_client.mint_session(SS.user_id))
    SS.session_token = token

    box = _StreamBox()
    SS.stream_node_id = node_id
    SS.stream_box = box
    # The frontend's tree cache only ever UPDATES an existing entry on a
    # patch push (see index.jsx's mergeById/patch handling) — it can never
    # ADD one. If node_id (or its connecting edge) was just created this
    # same run — e.g. answer_question's new child — the frontend has never
    # seen it via a full sync, so every patch during the whole stream would
    # be a silent no-op for it: nothing would appear until the stream ends
    # and the next render happens to be a full sync. Force exactly one full
    # sync on the first tick of every stream (cheap now — mergeById reuses
    # references for everything unchanged) so the frontend's cache is
    # guaranteed current before any patches for this node start landing.
    SS.stream_needs_full_sync = True
    threading.Thread(
        target=_stream_worker, args=(box, token, node_id, message), daemon=True
    ).start()
    return True


def tick_stream() -> Optional[Dict[str, Any]]:
    """Called once per fragment tick (app.py's _canvas_fragment). Copies the
    streaming node's latest content out of the box and pushes it as a
    patch; finalizes and persists once the thread marks the box done. If
    this node was part of a cascade, chains to the next node in the same
    tick via _advance_cascade. Once NOTHING is left streaming, triggers a
    full st.rerun() so app.py picks interval=None next time and the
    fragment stops ticking (otherwise it would keep re-syncing the whole
    tree every ~120ms forever — run_every is fixed at decoration time and
    doesn't turn itself off).

    Returns whatever canvas gesture (if any) came back with this push —
    the user can still interact with OTHER nodes while one streams."""
    node_id = SS.get("stream_node_id")
    if not node_id:
        return None
    box: _StreamBox = SS.stream_box
    with box.lock:
        blocks = copy.deepcopy(box.blocks)
        route, error, round_num, done = box.route, box.error, box.round, box.done

    idx = node_index(node_id)
    if idx is None:  # node deleted mid-stream
        SS.stream_node_id = None
        SS.stream_box = None
        if SS.get("cascade_order"):
            SS.cascade_failed.add(node_id)
            _advance_cascade()
        if not SS.get("stream_node_id"):
            st.rerun()
        return None

    node_data = SS.nodes[idx]["data"]
    node_data["blocks"] = blocks
    node_data["route"] = route

    # Capture (and consume) BEFORE _advance_cascade() runs below — that call
    # may start a fresh stream for the NEXT node via start_stream(), which
    # sets this flag again for THAT node. If we read it after, a cascade
    # transition would incorrectly attribute the new node's "needs a full
    # sync" flag to this (finishing) node's push, and the new node's actual
    # first tick would wrongly patch instead — reintroducing the invisible-
    # new-node bug this flag exists to fix.
    needs_full_sync = SS.get("stream_needs_full_sync", False)
    SS.stream_needs_full_sync = False

    if done:
        node_data["error"] = error
        node_data["round"] = round_num
        node_data["status"] = "error" if (error and not blocks) else "complete"
        SS.backend_ok = not error.startswith("Cannot reach")
        SS.stream_node_id = None
        SS.stream_box = None
        _persist()
        if SS.get("cascade_order"):
            if error and not blocks:
                SS.cascade_failed.add(node_id)
            _advance_cascade()  # may immediately start the next node's stream

    if needs_full_sync:
        result = chat_tree_canvas(
            SS.nodes,
            SS.edges,
            mode=SS.mode,
            cascading_reruns=CASCADING_RERUNS,
            height=760,
            key="chat_tree_canvas",
        )
    else:
        result = chat_tree_canvas(
            None,
            None,
            mode=SS.mode,
            cascading_reruns=CASCADING_RERUNS,
            height=760,
            key="chat_tree_canvas",
            patch={"node_id": node_id, "data": copy.deepcopy(node_data)},
        )
    if done and not SS.get("stream_node_id"):
        st.rerun()
    return result


def _reject_if_streaming() -> bool:
    """True (and toasts) if a live stream is already in flight — caller
    should bail out rather than starting another. Supporting genuinely
    concurrent multi-node live streams was judged out of scope."""
    if SS.get("stream_node_id"):
        st.toast(
            "Wait for the current response to finish before starting another.",
            icon=":material/hourglass_top:",
        )
        return True
    return False


def submit_prompt(node_id: str, prompt: str) -> None:
    prompt = (prompt or "").strip()
    if not prompt:
        return
    if _reject_if_streaming():
        return
    idx = node_index(node_id)
    if idx is None:
        return
    SS.nodes[idx]["data"]["prompt"] = prompt
    _persist()  # prompt is part of the DAG shape
    # No st.status spinner here — the node itself shows the "streaming" pulse
    # (ChatCanvas.jsx) live, same as app_rx.
    start_stream(node_id)


def _clear_round_if_needed(node_id: str) -> bool:
    """Drop the old exchange server-side so node_id holds only the fresh
    round (the node model assumes one round per conversation). Returns
    False only when clearing failed AND the node had a completed answer to
    protect — an "error" status's exchange is disposable, so a failed clear
    there is swallowed and the rerun proceeds anyway."""
    idx = node_index(node_id)
    if idx is None:
        return False
    data = SS.nodes[idx]["data"]
    node_status = data.get("status")
    if node_status not in ("complete", "error"):
        return True
    round_num = data.get("round") or 0
    try:
        token = SS.get("session_token") or _run(api_client.mint_session(SS.user_id))
        SS.session_token = token
        _run(api_client.delete_round(token, node_id, round_num))
        return True
    except Exception as exc:  # noqa: BLE001
        if node_status == "complete":
            data["error"] = f"Clear failed: {exc}"
            return False
        return True


def _advance_cascade() -> None:
    """Move SS.cascade_order forward to the next runnable node and start its
    live stream — called both to kick off the first node (from start_rerun)
    and to chain to the next one once tick_stream sees the current node's
    box marked done. Skips nodes with no prompt or whose parent already
    failed (same failure-pruning semantics the old blocking rerun_subtree
    had: a failed branch prunes, sibling branches continue). Clears the
    SS.cascade_* bookkeeping once the order is exhausted."""
    order = SS.get("cascade_order")
    if not order:
        return
    pos = SS.cascade_pos + 1
    while pos < len(order):
        nid = order[pos]
        idx = node_index(nid)
        parent = tree.parent_of(SS.edges, nid)
        if idx is None or parent in SS.cascade_failed:
            SS.cascade_failed.add(nid)
            pos += 1
            continue
        if not (SS.nodes[idx]["data"].get("prompt") or "").strip():
            pos += 1
            continue  # never-submitted node: nothing to rerun
        if not _clear_round_if_needed(nid):
            SS.cascade_failed.add(nid)
            pos += 1
            continue
        SS.cascade_pos = pos
        start_stream(nid, SS.cascade_include_ancestors)
        return

    # Exhausted — cascade complete.
    total = len(order)
    failed = len(SS.cascade_failed)
    if total > 1:
        succeeded = total - failed
        st.toast(
            f"Rerun complete ({succeeded}/{total} succeeded)",
            icon=":material/warning:" if failed else ":material/check:",
        )
    SS.cascade_order = None
    SS.cascade_pos = -1
    SS.cascade_failed = set()


def start_rerun(node_id: str, cascade: bool, include_ancestors: bool) -> None:
    """Entry point for the "Rerun Node?" dialog's confirm button, dispatched
    from app.py's pending_rerun handling AFTER the dialog has already
    closed (so it isn't trapped behind a modal while it runs). One node or
    a full cascade both stream live now — _advance_cascade chains through
    SS.cascade_order one node at a time (parent before child, matching
    include_ancestors), starting each via the same start_stream/tick_stream
    mechanism submit_prompt uses. Starting the first node here, immediately,
    means it's already in flight by the time app.py computes the canvas
    fragment's run_every below.
    """
    if node_index(node_id) is None:
        return
    if _reject_if_streaming():
        return
    order = tree.bfs_order(SS.edges, node_id) if cascade else [node_id]
    SS.cascade_order = order
    SS.cascade_pos = -1
    SS.cascade_include_ancestors = include_ancestors
    SS.cascade_failed = set()
    _advance_cascade()


# ── sessions ─────────────────────────────────────────────────

def new_session() -> None:
    SS.session_id = dag_store.create_session(SS.user_id)
    SS.nodes = []
    SS.edges = []
    _persist()


def list_sessions() -> List[Dict[str, Any]]:
    rows = dag_store.list_sessions(SS.user_id)
    sessions = []
    for row in rows:
        title = " ".join((row["first_prompt"] or "").split())
        if len(title) > 60:
            title = title[:60].rstrip() + "…"
        sessions.append(
            {
                "sid": row["session_id"],
                "short": row["session_id"][:8],
                "when": _time.strftime("%Y-%m-%d %H:%M", _time.localtime(row["updated_at"])),
                "nodes": row["node_count"],
                "title": title or "(empty)",
                "current": row["session_id"] == SS.session_id,
            }
        )
    return sessions


def load_session(sid: str) -> Tuple[str, int]:
    """Returns (error_message, missing_count); error '' on success."""
    sid = (sid or "").strip()
    if not sid:
        return "Enter a session ID.", 0
    owner = dag_store.session_owner(sid)
    if owner is None or owner != SS.user_id:
        # Same policy as the backend: don't reveal foreign sessions.
        return "No such session.", 0
    SS.session_id = sid
    missing = _hydrate_session(sid)
    return "", missing


# ── export / import ──────────────────────────────────────────

def export_session() -> Tuple[str, int]:
    """Returns (json_text, fetch_failure_count)."""
    sid, uid = SS.session_id, SS.user_id
    nodes = copy.deepcopy([dict(n) for n in SS.nodes])
    edges = [dict(e) for e in SS.edges]
    token = SS.get("session_token") or ""
    if not token:
        try:
            token = _run(api_client.mint_session(uid))
            SS.session_token = token
        except Exception:  # noqa: BLE001 — export still works, sans answers
            token = ""

    async def _fetch_all():
        histories: Dict[str, Any] = {}
        failures = 0
        for node in nodes:
            if not (node["data"].get("prompt") or "").strip():
                continue  # never submitted — no history to fetch
            if token:
                try:
                    histories[node["id"]] = await api_client.get_history(token, node["id"])
                    continue
                except Exception:  # noqa: BLE001
                    pass
            histories[node["id"]] = None
            failures += 1
        return histories, failures

    histories, failures = _run(_fetch_all())
    doc = exporter.build_export(sid, uid, nodes, edges, histories)
    return json.dumps(doc, indent=2, ensure_ascii=False), failures


def import_session(raw: bytes) -> Tuple[str, int, int]:
    """Import a v1 export file as a NEW session. Returns
    (error_message, node_count, missing_count); error '' on success."""
    try:
        canvas = exporter.parse_import(raw)
    except ValueError as exc:
        return str(exc), 0, 0

    SS.session_id = dag_store.create_session(SS.user_id)
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
    SS.nodes = nodes
    SS.edges = [{"id": e["edge_id"], "source": e["source"], "target": e["target"]} for e in canvas["edges"]]
    _persist()
    missing = _hydrate_session(SS.session_id)
    return "", len(nodes), missing


# ── canvas event dispatch ────────────────────────────────────

def handle_event(event: Dict[str, Any]) -> bool:
    """Dispatch one canvas gesture. Returns True whenever the caller should
    st.rerun() — which is every branch that mutated session_state, including
    the confirmation-gate branches below.

    Why clear_node/delete_node ALSO need True: main() checks
    `if SS.clear_target: _confirm_clear_dialog()` near the TOP of the
    script, before the canvas is rendered and this dispatcher runs. Setting
    the flag here does not retroactively open the dialog on THIS run — the
    check already happened. Returning False (the original, buggy behavior)
    left the dialog invisible until some UNRELATED later interaction
    happened to cause a rerun, which read as "I have to click twice."
    Returning True makes app.py call st.rerun() immediately, so the very
    next run's top-of-script check sees the flag and opens the dialog —
    one extra script execution, invisible to the user, still one click.
    """
    etype = event["type"]
    payload = event["payload"]

    if etype == "submit_prompt":
        submit_prompt(*payload)
    elif etype == "branch_node":
        branch_node(*payload)
    elif etype == "branch_from_text":
        branch_node_from_text(*payload)
    elif etype == "duplicate_node":
        error = duplicate_node(*payload)
        if error:
            st.toast(error, icon=":material/error:")
    elif etype == "clear_node":
        (node_id,) = payload
        if node_index(node_id) is None:
            return False  # nothing changed — no rerun needed
        if not CASCADING_RERUNS and tree.children_of(SS.edges, node_id):
            st.toast("Only leaf nodes can be cleared.", icon=":material/error:")
            return False  # toast already visible this run — no rerun needed
        SS.clear_target = node_id
        # Reset to the default scope (both on) each time the dialog opens.
        SS.clear_cascade = True
        SS.clear_include_ancestors = True
    elif etype == "delete_node":
        (node_id,) = payload
        if node_index(node_id) is None:
            return False  # nothing changed — no rerun needed
        SS.delete_target = node_id
    elif etype == "add_root":
        add_root(*payload)
    elif etype == "node_moved":
        node_moved(*payload)
    elif etype == "node_resized":
        node_resized(*payload)
    elif etype == "connect_edge":
        error = connect_edge(*payload)
        if error:
            st.toast(error, icon=":material/error:")
    elif etype == "edge_reconnect":
        error = reconnect_edge(*payload)
        if error:
            st.toast(error, icon=":material/error:")
    elif etype == "edge_delete":
        delete_edge(*payload)
    elif etype == "answer_question":
        answer_question(*payload)
    return True

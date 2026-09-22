# Copyright (c) 2026 Arvindra Sehmi
# Licensed under the MIT License — see LICENSE for details.

"""chat_tree_canvas_st — Streamlit custom component (components v2) hosting
the chat-tree-canvas-react canvas.

The canvas is a controlled component: the host owns the tree (nodes/edges in
ReactFlow shape, typed blocks — see chat_tree.blocks) and receives user
intents back as a single transient trigger value. One event per script rerun:

    event = chat_tree_canvas(nodes, edges, mode="dark", key="canvas")
    if event is not None:
        event["type"]     # "submit_prompt" | "branch_node" | "branch_from_text"
                          # | "duplicate_node" | "clear_node" | "delete_node"
                          # | "add_root" | "node_moved" | "node_resized"
                          # | "connect_edge" | "edge_reconnect" | "edge_delete"
        event["payload"]  # positional args, same order as the React callbacks
        event["nonce"]    # unique per gesture — dedupe across reruns if caching

For live token streaming into one node without re-shipping the whole tree,
call again with `patch=` instead of `nodes`/`edges`, reusing the SAME `key`
so it lands on the same mounted component instance instead of remounting
it — no `st.empty()` placeholder is needed for this; the key alone gives
cross-render identity. Each call is one script run (see
_pm/LIVE_TOKEN_STREAMING_IN_APP_ST.md for why: a keyed v2 component can
only be rendered once per script run):

    chat_tree_canvas(nodes, edges, key="canvas")   # full sync, one run
    ...
    chat_tree_canvas(None, None, key="canvas",     # patch, a LATER run
                      patch={"node_id": nid, "data": node["data"]})

The frontend (index.jsx) caches the last full-sync tree per component
instance and merges a patch's `data` into just that one node, keeping every
other node's object reference untouched — see index.jsx's `treeCache`.

Build the frontend first (see frontend/README section in the package README):
    cd packages/chat-tree-canvas-streamlit/frontend && npm install && npm run build
"""

from typing import Any, Dict, List, Optional

import streamlit as st

# Fully qualified component key: "<distribution-name>.<package-name>".
_COMPONENT_KEY = "chat-tree-canvas-st.chat_tree_canvas_st"

_renderer = None


def _get_renderer():
    global _renderer
    if _renderer is None:
        _renderer = st.components.v2.component(
            _COMPONENT_KEY,
            html='<div class="ctc-st-root"></div>',
            # Vite build outputs (globs must match exactly one file each):
            js="index-*.js",
            css="style-*.css",
            # Keep Streamlit's page styles out of the canvas; the component
            # carries its own CSS (ReactFlow base styles + palette).
            isolate_styles=True,
        )
    return _renderer


def chat_tree_canvas(
    nodes: Optional[List[Dict[str, Any]]],
    edges: Optional[List[Dict[str, Any]]],
    *,
    mode: str = "dark",
    cascading_reruns: bool = True,
    height: int = 760,
    key: str = "chat_tree_canvas",
    patch: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Render the canvas; return the user's intent event for this rerun.

    Returns None when the rerun wasn't caused by a canvas gesture (trigger
    values are transient — they reset after one script run).

    Pass `patch={"node_id": ..., "data": ...}` instead of `nodes`/`edges` to
    push a live update into ONE node (e.g. one streamed token) without
    re-serializing the whole tree — see the module docstring. `nodes`/`edges`
    are ignored (may be None) when `patch` is given.
    """
    data: Dict[str, Any] = {
        "mode": mode,
        "cascadingReruns": cascading_reruns,
        "height": height,
    }
    if patch is not None:
        data["patch"] = patch
    else:
        data["nodes"] = nodes
        data["edges"] = edges
    result = _get_renderer()(
        key=key,
        data=data,
        width="stretch",
        height=height,
    )
    return result.get("event")

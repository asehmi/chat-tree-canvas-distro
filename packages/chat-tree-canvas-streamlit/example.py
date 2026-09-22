# Copyright (c) 2026 Arvindra Sehmi
# Licensed under the MIT License — see LICENSE for details.

"""Minimal smoke-test app for the chat_tree_canvas_st component.

Run from the repo root:
    .venv/Scripts/streamlit run packages/chat-tree-canvas-streamlit/example.py

Renders a tiny static tree and echoes every canvas event. No backend, no
persistence — just the component contract end to end.
"""

import streamlit as st

from chat_tree_canvas_st import chat_tree_canvas

st.set_page_config(page_title="chat-tree-canvas-st example", layout="wide")
st.title("chat-tree-canvas-st — component smoke test")

if "nodes" not in st.session_state:
    st.session_state.nodes = [
        {
            "id": "root1",
            "type": "chat",
            "position": {"x": 0, "y": 0},
            "data": {
                "prompt": "What is a tree?",
                "status": "complete",
                "blocks": [
                    {"kind": "text", "text": "A **tree** is a connected acyclic graph."},
                    {"kind": "metric", "label": "Nodes", "value": 2, "unit": ""},
                ],
                "error": "",
                "route": "demo",
                "round": 0,
                "leaf": False,
                "width": 0,
                "height": 0,
            },
        },
        {
            "id": "child1",
            "type": "chat",
            "position": {"x": 60, "y": 320},
            "data": {
                "prompt": "",
                "status": "idle",
                "blocks": [],
                "error": "",
                "route": "",
                "round": 0,
                "leaf": True,
                "width": 0,
                "height": 0,
            },
        },
    ]
    st.session_state.edges = [{"id": "e-root1-child1", "source": "root1", "target": "child1"}]

mode = st.sidebar.radio("Mode", ["dark", "light"], index=0)

event = chat_tree_canvas(
    st.session_state.nodes,
    st.session_state.edges,
    mode=mode,
    height=640,
    key="demo_canvas",
)

st.sidebar.subheader("Last event")
st.sidebar.json(event or {"event": None})

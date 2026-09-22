# Copyright (c) 2026 Arvindra Sehmi
# Licensed under the MIT License — see LICENSE for details.

"""app_st — the Streamlit host for chat-tree-canvas.

Mirrors app_rx (the Reflex host) feature-for-feature, using the same
chat_tree core and the same chat-tree-canvas-react package (wrapped for
Streamlit as the chat_tree_canvas_st custom component). Sessions are stored
in the same .db/canvas.db as app_rx, so a session created in one host loads
identically in the other.

Run:
    .venv\\Scripts\\streamlit run app_st/app.py
"""

# Copyright (c) 2026 Arvindra Sehmi
# Licensed under the MIT License — see LICENSE for details.

"""Sidebar chrome for the Streamlit host: identity, session picker, new
session, export/import, tidy tree, theme toggle, backend status — the
Streamlit-native counterpart to app_rx/pages/index.py's toolbar()."""

from __future__ import annotations

import streamlit as st

from app_st import auth, controller
from chat_tree import api_client
from chat_tree.blocks import missing_content_warning

SS = st.session_state


@st.dialog("Load Session")
def _load_session_dialog() -> None:
    st.caption("Click a session to load its conversation tree, or paste a session ID.")
    for row in controller.list_sessions():
        label = f"`{row['short']}`  ·  {row['when']}  ·  {row['nodes']} nodes"
        if row["current"]:
            label += "  **(current)**"
        if row["title"]:
            label += f"\n\n{row['title']}"
        # The "current" row stays clickable on purpose: it reflects the DB,
        # but the canvas on screen only reflects whatever was last
        # hydrated into this session — which can go stale if the
        # session's content changed elsewhere (another host, another
        # tab, a backend restart) while this one stayed open. Clicking it
        # re-hydrates from the DB same as any other row, which is the
        # only way to force that refresh short of a full page reload.
        if st.button(
            label,
            key=f"pick_{row['sid']}",
            use_container_width=True,
        ):
            error, missing = controller.load_session(row["sid"])
            if error:
                st.error(error)
            else:
                if missing:
                    st.toast(missing_content_warning(missing), icon=":material/warning:")
                st.rerun()
    st.divider()
    sid = st.text_input("…or paste a session ID", key="manual_sid_input")
    if st.button("Load", key="manual_load_btn"):
        error, missing = controller.load_session(sid)
        if error:
            st.error(error)
        else:
            if missing:
                st.toast(missing_content_warning(missing), icon=":material/warning:")
            st.rerun()


@st.dialog("Import Session")
def _import_session_dialog() -> None:
    st.caption(
        "Upload a chat-tree-canvas JSON export. It becomes a new session; "
        "node responses re-hydrate from the backend history where available."
    )
    uploaded = st.file_uploader("Session JSON", type="json", key="import_file")
    col1, col2 = st.columns(2)
    if col1.button("Cancel", use_container_width=True, key="import_cancel_btn"):
        st.rerun()
    if col2.button("Import", type="primary", use_container_width=True, key="import_confirm_btn"):
        if uploaded is None:
            st.error("Choose a JSON export file first.")
            return
        error, count, missing = controller.import_session(uploaded.getvalue())
        if error:
            st.error(error)
            return
        st.toast(
            f"Imported {count} node(s) into session {SS.session_id[:8]}…",
            icon=":material/check_circle:",
        )
        if missing:
            st.toast(missing_content_warning(missing), icon=":material/warning:")
        st.rerun()


def render() -> None:
    with st.sidebar:
        st.title(":material/hub: Chat Tree Canvas")
        st.caption("Streamlit host — shares sessions with the Reflex app")

        if auth.auth0_configured():
            auth.sign_out_button()

        st.divider()

        if st.button(":material/add: New Chat", type="primary", use_container_width=True):
            controller.add_root_auto()
            st.rerun()

        if st.button(":material/restart_alt: New Session", use_container_width=True):
            controller.new_session()
            st.rerun()

        if st.button(":material/folder_open: Load Session…", use_container_width=True):
            _load_session_dialog()

        if st.button(":material/park: Tidy Tree", use_container_width=True):
            controller.tidy_tree()
            st.rerun()

        st.divider()

        # Export/import icons mirror app_rx's convention: export = upload
        # arrow (pushing your session out), import = download arrow
        # (bringing a file in).
        if st.button(":material/upload: Export Session", use_container_width=True):
            with st.spinner("Fetching node histories…"):
                data, failures = controller.export_session()
            if failures:
                st.warning(
                    f"Exported, but history for {failures} node(s) was "
                    "unavailable — their conversations are marked partial."
                )
            st.download_button(
                ":material/download: Download JSON",
                data=data,
                file_name=f"chat-tree-{SS.session_id[:8]}.json",
                mime="application/json",
                use_container_width=True,
            )

        if st.button(":material/download: Import Session", use_container_width=True):
            _import_session_dialog()

        st.divider()

        light = st.toggle(":material/light_mode: Light mode", value=(SS.mode == "light"))
        new_mode = "light" if light else "dark"
        if new_mode != SS.mode:
            SS.mode = new_mode
            st.rerun()

        st.divider()

        canvas_height = st.slider(":material/height: Canvas height", value=SS.canvas_height, min_value=600, max_value=2000, step=50, help="Adjust the canvas height to fit your screen and workflow.")
        if canvas_height != SS.canvas_height:
            SS.canvas_height = canvas_height
            st.rerun()

        st.divider()
        st.caption(f"User: `{SS.user_id}`")
        backend_label = f"{api_client.provider_name()} @ {api_client.base_url()}"
        if SS.backend_ok:
            st.success(f"Backend online · {backend_label}")
        else:
            st.error(f"Backend unreachable · {backend_label}")

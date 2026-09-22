# Copyright (c) 2026 Arvindra Sehmi
# Licensed under the MIT License — see LICENSE for details.

"""chat-tree-canvas — Streamlit host.

Same tree model as app_rx (the Reflex host): conversations are trees of
prompt/answer exchanges rendered on a ReactFlow canvas via the
chat_tree_canvas_st custom component. All domain logic — validation,
streaming, cascading reruns, tidy layout, export/import, SQLite persistence
— lives in the shared chat_tree package and is invoked through
app_st/controller.py; this file is UI wiring only.

Run (from the repo root):
    .venv\\Scripts\\streamlit run app_st/app.py

Requires the component frontend to be built once:
    cd packages/chat-tree-canvas-streamlit/frontend && npm install && npm run build

Sessions are stored in the SAME .db/canvas.db as app_rx — loading the same
session_id in both hosts shows the same tree (nodes created in the Reflex
app are visible here, and vice versa).
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

# `streamlit run app_st/app.py` only ever puts THIS script's own directory
# (app_st/) on sys.path (see streamlit.web.bootstrap._fix_sys_path) — unlike
# Reflex, which manages sys.path itself via rxconfig.py. The repo root (where
# the app_st and chat_tree packages actually live) must be added by hand,
# before any repo-local import, regardless of the CWD the command was run
# from. chat_tree_canvas_st is unaffected — it's pip-installed (editable).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv # NOQA
# .env lives at the repo root; app_st/ is one level below it (mirrors
# rxconfig.py's own explicit load — Streamlit does not auto-load .env).
load_dotenv(_REPO_ROOT / ".env")

from app_st import auth, controller, sidebar # NOQA
from chat_tree.config import CASCADING_RERUNS # NOQA
from chat_tree_canvas_st import chat_tree_canvas # NOQA

st.set_page_config(page_title="Chat Tree Canvas", page_icon=":material/hub:", layout="wide")

SS = st.session_state
if "canvas_height" not in SS:
    SS.canvas_height = 800
if "mode" not in SS:
    SS.mode = "dark"
if "clear_target" not in SS:
    SS.clear_target = ""
if "clear_cascade" not in SS:
    SS.clear_cascade = True
if "clear_include_ancestors" not in SS:
    SS.clear_include_ancestors = True
if "delete_target" not in SS:
    SS.delete_target = ""
if "last_event_nonce" not in SS:
    SS.last_event_nonce = None
if "pending_rerun" not in SS:
    SS.pending_rerun = None


@st.dialog("Rerun Node?")
def _confirm_clear_dialog() -> None:
    st.write(
        "This deletes the node's response from the backend history and "
        "reruns its prompt. This cannot be undone."
    )
    st.checkbox("Apply to descendants", key="clear_cascade")
    st.caption(
        "Also clear and rerun every descendant node (breadth-first), so "
        "their answers refresh too."
    )
    st.checkbox("Include ancestors as context", key="clear_include_ancestors")
    st.caption(
        "Prefix each rerun prompt with its ancestor chain's answers, as normal."
    )
    st.markdown(
        f"**{controller.clear_scope_message(SS.clear_cascade, SS.clear_include_ancestors)}**"
    )
    col1, col2 = st.columns(2)
    if col1.button("Cancel", use_container_width=True, key="clear_cancel_btn"):
        SS.clear_target = ""
        st.rerun()
    if col2.button("Rerun", type="primary", use_container_width=True, key="clear_confirm_btn"):
        # Defer the actual rerun to the next script run (main()'s
        # pending_rerun handling) instead of running it here: this dialog
        # is a blocking modal, and clearing the old round is a network call
        # — better to close the dialog immediately than sit there frozen.
        SS.pending_rerun = {
            "node_id": SS.clear_target,
            "cascade": SS.clear_cascade,
            "include_ancestors": SS.clear_include_ancestors,
        }
        SS.clear_target = ""
        st.rerun()


@st.dialog("Delete Node?")
def _confirm_delete_dialog() -> None:
    st.write(controller.delete_message(SS.delete_target))
    col1, col2 = st.columns(2)
    if col1.button("Cancel", use_container_width=True, key="delete_cancel_btn"):
        SS.delete_target = ""
        st.rerun()
    if col2.button("Delete", type="primary", use_container_width=True, key="delete_confirm_btn"):
        controller.confirm_delete(SS.delete_target)
        SS.delete_target = ""
        st.rerun()


def main() -> None:
    user_id = auth.require_identity()
    controller.ensure_loaded(user_id)

    # Confirmation gates: re-invoked every rerun while the target is set, so
    # the dialog stays open across its own internal interactions regardless
    # of whether the ORIGINAL triggering click happens again this run.
    if SS.clear_target:
        _confirm_clear_dialog()
    if SS.delete_target:
        _confirm_delete_dialog()

    sidebar.render()

    st.title(":material/hub: Chat Tree Canvas")
    status = "🟢 online" if SS.backend_ok else "🔴 unreachable"
    st.caption(f"Session `{SS.session_id[:8]}` · backend: {status}")

    # Above the canvas (not below it) — the canvas is a fixed 760px-tall
    # component, so a placeholder positioned after it can end up below the
    # fold depending on viewport height.
    cascade_status_slot = st.empty()

    if SS.pending_rerun:
        req = SS.pending_rerun
        SS.pending_rerun = None
        controller.start_rerun(req["node_id"], req["cascade"], req["include_ancestors"])

    # A *keyed* v2 component can only be updated once per script run — see
    # controller.py's streaming section — so live token pushes have to
    # happen as separate reruns, one per tick, via st.fragment(run_every=).
    # run_every is fixed at decoration time, so making it conditional means
    # re-decorating a fresh local function each time main() runs: fast only
    # while a stream is in flight (controller.start_stream/start_rerun),
    # otherwise this behaves exactly like a plain (non-fragment) call.
    #
    # NOTE: this call is bare — no st.empty() wrapper. The original
    # pre-streaming code never had one either; the explicit key= alone is
    # what gives it stable identity across renders (confirmed by reading
    # Streamlit's own source: a keyed bidi component's registration id is
    # computed from the key alone). An st.empty() wrapper was added earlier
    # in this feature's development for a since-abandoned design (calling
    # the component repeatedly inside one script run) and never removed —
    # it turned out to be extra unnecessary DOM nesting, and a real
    # candidate for the edge-misalignment bug this session has been
    # chasing (two other theories — React.memo, fragment-only-when-
    # streaming — were tried and ruled out; see
    # _pm/LIVE_TOKEN_STREAMING_IN_APP_ST.md for the full history).
    interval = 0.12 if SS.get("stream_node_id") else None

    @st.fragment(run_every=interval)
    def _canvas_fragment() -> None:
        if SS.get("stream_node_id"):
            event = controller.tick_stream()
        else:
            event = chat_tree_canvas(
                SS.nodes,
                SS.edges,
                mode=SS.mode,
                cascading_reruns=CASCADING_RERUNS,
                height=SS.canvas_height,
                key="chat_tree_canvas",
            )

        if SS.get("cascade_order"):
            failed = len(SS.cascade_failed)
            with cascade_status_slot:
                st.caption(
                    f"Rerunning node {SS.cascade_pos + 1} of {len(SS.cascade_order)}"
                    + (f" · {failed} failed" if failed else "")
                )
        else:
            cascade_status_slot.empty()

        # Trigger values are transient (reset after one script run/tick), but
        # dedupe by nonce anyway — belt and suspenders against any double-fire.
        if event is not None and event.get("nonce") != SS.last_event_nonce:
            SS.last_event_nonce = event["nonce"]
            if controller.handle_event(event):
                st.rerun()  # full app rerun, even though called from a fragment

    _canvas_fragment()


if __name__ == "__main__":
    main()

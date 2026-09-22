# Copyright (c) 2026 Arvindra Sehmi
# Licensed under the MIT License — see LICENSE for details.

"""Single-page canvas UI: full-viewport ReactFlow canvas with a top menu bar
(title, identity, session actions, backend status, theme toggle, sign in/out)
plus the load-session and clear/delete-node dialogs.

Styled with Tailwind utility classes on top of the semantic theme colors
(assets/css/themes.css + assets/css/styles.css, "Performance" theme).
"""

import reflex as rx

from app_rx.components import chat_canvas
from chat_tree import api_client
from app_rx.states import AuthState, CanvasState, ThemeState
from chat_tree.config import CASCADING_RERUNS
from app_rx.states.theme_state import compose_theme_init_script

# Resolved once at startup — which chat backend the canvas talks to.
_BACKEND = f"{api_client.provider_name()} @ {api_client.base_url()}"

_BTN = (
    "cursor-pointer rounded-lg border border-dim/50 bg-transparent px-3 py-1.5 "
    "text-sm font-medium text-dark hover:bg-secondary/30 transition-colors "
    "gap-2 whitespace-nowrap flex items-center"
)

_PRIMARY_BTN = (
    "cursor-pointer rounded-lg border-none bg-primary-accent px-3.5 py-1.5 "
    "text-sm font-semibold text-on-accent hover:bg-primary transition-colors "
    "gap-2 whitespace-nowrap flex items-center"
)

_DANGER_BTN = (
    "cursor-pointer rounded-lg border-none bg-red-500 px-3.5 py-1.5 "
    "gap-2 flex items-center text-sm font-semibold text-white hover:bg-red-600 transition-colors"
)


def _chip(label: rx.Component | str, value, title) -> rx.Component:
    return rx.box(
        rx.text(label, class_name="text-[10px] tracking-wider text-dark"),
        rx.text(value, class_name="font-mono text-xs text-default"),
        title=title,
        class_name="rounded-lg border border-dim/40 px-2.5 py-1",
    )


def theme_toggle() -> rx.Component:
    """Light/dark mode toggle (Performance theme colors)."""
    return rx.el.button(
        rx.cond(
            ThemeState.selected_mode == "dark",
            rx.icon("sun", size=18),
            rx.icon("moon", size=18),
        ),
        on_click=ThemeState.toggle_mode,
        title="Toggle light / dark mode",
        class_name=(
            "cursor-pointer rounded-lg border border-dim/50 bg-transparent p-2 "
            "text-default hover:bg-secondary/30 transition-colors flex items-center"
        ),
    )


def user_controls() -> rx.Component:
    """Sign in / sign out (Auth0), adapted from actionwave's sidebar_user_button."""
    return rx.cond(
        AuthState.is_user_authenticated,
        rx.hstack(
            rx.el.button(
                AuthState.name_initials,
                title=AuthState.user_email,
                class_name=(
                    "flex h-8 w-8 items-center justify-center rounded-full "
                    "bg-primary text-xs font-semibold text-white"
                ),
            ),
            rx.el.button(
                rx.icon("log-out", size=16),
                rx.text("Sign Out"),
                on_click=AuthState.logout,
                class_name=(
                    "flex items-center gap-2 bg-primary-accent text-on-accent cursor-pointer "
                    "hover:bg-primary transition-colors duration-200 "
                    "py-1.5 px-3 rounded-md font-medium text-sm whitespace-nowrap"
                ),
            ),
            class_name="items-center gap-2",
        ),
        rx.el.button(
            rx.icon("log-in", size=16),
            rx.text("Sign In"),
            on_click=AuthState.login,
            class_name=(
                "flex items-center gap-2 bg-primary-accent text-on-accent cursor-pointer "
                "hover:bg-primary transition-colors duration-200 "
                "py-1.5 px-3 rounded-md font-medium text-sm whitespace-nowrap"
            ),
        ),
    )


def toolbar() -> rx.Component:
    return rx.box(
        rx.text(
            "Chat Tree Canvas",
            class_name="text-[15px] font-semibold text-default whitespace-nowrap",
        ),
        rx.box(
            class_name="h-2 w-2 shrink-0 rounded-full "
            + rx.cond(CanvasState.backend_ok, "bg-green-500", "bg-red-500"),
            title=rx.cond(
                CanvasState.backend_ok,
                f"Backend connected ({_BACKEND})",
                f"Backend unreachable — start the chat provider ({_BACKEND})",
            ),
        ),
        _chip("User", CanvasState.user_id, "Current user ID"),
        _chip("Session", CanvasState.session_short, CanvasState.session_id),
        rx.el.button(
            rx.icon("plus", size=16),
            rx.text("New Chat"),
            on_click=CanvasState.add_root_auto,
            class_name=_PRIMARY_BTN,
        ),
        rx.el.button(
            rx.icon("rotate-ccw", size=16),
            rx.text("New Session"),
            on_click=CanvasState.new_session,
            class_name=_BTN,
            title="Start a fresh canvas under a new session ID",
        ),
        rx.el.button(
            rx.icon("folder-open", size=16),
            rx.text("Load Session…"),
            on_click=CanvasState.open_load_dialog,
            class_name=_BTN,
            title="Load a previous session's DAG by its session ID",
        ),
        rx.el.button(
            rx.icon("upload", size=16),
            rx.text("Export Session"),
            on_click=CanvasState.export_session,
            class_name=_BTN,
            title="Export this session as JSON (canvas shape + coalesced conversations)",
        ),
        rx.el.button(
            rx.icon("download", size=16),
            rx.text("Import Session"),
            on_click=CanvasState.open_import_dialog,
            class_name=_BTN,
            title="Import a session JSON export as a new session",
        ),
        rx.el.button(
            rx.icon("tree-pine", size=16),
            rx.text("Tidy Tree"),
            on_click=CanvasState.tidy_tree,
            class_name=_BTN,
            title="Tidy Tree: auto-layout — one row per depth, children centered under parents",
        ),
        rx.cond(
            CanvasState.rerun_total > 0,
            rx.box(
                rx.text(
                    "Rerunning "
                    + CanvasState.rerun_done.to_string()
                    + " of "
                    + CanvasState.rerun_total.to_string()
                    + "…",
                    class_name="text-xs font-medium text-default",
                ),
                title="Cascading rerun in progress (breadth-first over the subtree)",
                class_name=(
                    "animate-pulse rounded-lg border border-primary-accent/50 "
                    "bg-secondary/40 px-2.5 py-1"
                ),
            ),
        ),
        rx.el.div(class_name="flex-1"),
        theme_toggle(),
        user_controls(),
        class_name=(
            "absolute top-0 left-0 right-0 z-10 flex items-center gap-3 "
            "border-b border-dim/40 bg-light/90 px-4 py-2 "
            "backdrop-blur-md shadow-sm"
        ),
    )


def _session_row(s: dict) -> rx.Component:
    """One row in the session picker. Clicking selects it (fills the ID field
    and highlights the row); OK loads it. The current session is marked with
    a filled badge + primary border and can't be re-selected."""
    base = (
        "flex w-full cursor-pointer flex-col gap-0.5 rounded-lg border "
        "px-3 py-2 text-left hover:bg-secondary/30 transition-colors "
        "disabled:cursor-default "
    )
    return rx.el.button(
        rx.hstack(
            rx.text(s["short"], class_name="font-mono text-xs text-primary-accent"),
            rx.text(s["when"], class_name="text-xs text-dim whitespace-nowrap"),
            rx.text(s["nodes"] + " nodes", class_name="text-xs text-dim whitespace-nowrap"),
            rx.cond(
                s["current"] != "",
                rx.text(
                    "Current",
                    class_name=(
                        "rounded bg-primary px-1.5 py-0.5 text-[10px] "
                        "font-semibold text-on-accent"
                    ),
                ),
            ),
            rx.cond(
                CanvasState.load_sid_value == s["sid"],
                rx.text(
                    "Selected",
                    class_name=(
                        "rounded bg-primary-accent px-1.5 py-0.5 text-[10px] "
                        "font-semibold text-on-accent"
                    ),
                ),
            ),
            class_name="w-full items-center gap-3",
        ),
        rx.text(s["title"], class_name="w-full truncate text-left text-xs text-default"),
        # The "current" session stays clickable on purpose: its row in this
        # list reflects the DB, but the canvas on screen only reflects
        # whatever was last hydrated into this state instance — which can
        # go stale if the session's content changed elsewhere (another
        # host, another tab, a backend restart) while this one stayed
        # open. Clicking it and confirming re-hydrates from the DB same as
        # any other row, which is the only way to force that refresh
        # short of a full page reload.
        on_click=CanvasState.load_session_pick(s["sid"]),
        type="button",
        class_name=rx.cond(
            s["current"] != "",
            base + "border-primary bg-secondary/40",
            rx.cond(
                CanvasState.load_sid_value == s["sid"],
                base + "border-primary-accent bg-secondary/20",
                base + "border-dim/40",
            ),
        ),
    )


def load_session_dialog() -> rx.Component:
    return rx.dialog.root(
        rx.dialog.content(
            rx.dialog.title("Load Session"),
            rx.dialog.description(
                "Click a session to select it, then OK to load its tree. "
                "Node responses are re-fetched from the backend history.",
                size="2",
                margin_bottom="12px",
            ),
            rx.box(
                rx.foreach(CanvasState.sessions, _session_row),
                class_name="flex max-h-64 flex-col gap-1.5 overflow-y-auto pr-1",
            ),
            rx.text(
                "…or paste a session ID:",
                class_name="mb-1 mt-3 text-xs text-dim",
            ),
            rx.input(
                placeholder="session id (hex)",
                value=CanvasState.load_sid_value,
                on_change=CanvasState.set_load_sid_value,
                width="100%",
                font_family="ui-monospace, monospace",
            ),
            rx.cond(
                CanvasState.load_error != "",
                rx.text(
                    CanvasState.load_error,
                    class_name="mt-1.5 text-sm text-red-500",
                ),
            ),
            rx.flex(
                rx.el.button(
                    "Cancel",
                    on_click=CanvasState.close_load_dialog,
                    class_name=_BTN,
                ),
                rx.el.button(
                    "OK",
                    on_click=CanvasState.load_session_confirm,
                    class_name=_PRIMARY_BTN,
                ),
                gap="8px",
                justify="end",
                margin_top="16px",
            ),
            max_width="420px",
        ),
        open=CanvasState.load_dialog_open,
    )


def import_session_dialog() -> rx.Component:
    return rx.dialog.root(
        rx.dialog.content(
            rx.dialog.title("Import Session"),
            rx.dialog.description(
                "Upload a chat-tree-canvas JSON export. It becomes a new "
                "session; node responses re-hydrate from the backend history "
                "where available.",
                size="2",
                margin_bottom="12px",
            ),
            rx.upload(
                rx.vstack(
                    rx.icon("file-json", size=28, class_name="text-primary-accent"),
                    rx.text(
                        "Drop the JSON file here, or click to browse",
                        class_name="text-sm text-default",
                    ),
                    rx.foreach(
                        rx.selected_files("session_import"),
                        lambda f: rx.text(
                            f, class_name="font-mono text-xs text-primary-accent"
                        ),
                    ),
                    class_name="items-center gap-2",
                ),
                id="session_import",
                max_files=1,
                accept={"application/json": [".json"]},
                class_name=(
                    "w-full cursor-pointer rounded-lg border-2 border-dashed "
                    "border-dim/50 px-4 py-6 hover:border-primary-accent "
                    "transition-colors"
                ),
            ),
            rx.flex(
                rx.el.button(
                    "Cancel",
                    on_click=[
                        rx.clear_selected_files("session_import"),
                        CanvasState.close_import_dialog,
                    ],
                    class_name=_BTN,
                ),
                rx.el.button(
                    "Import",
                    on_click=CanvasState.import_session_upload(
                        rx.upload_files(upload_id="session_import")
                    ),
                    class_name=_PRIMARY_BTN,
                ),
                gap="8px",
                justify="end",
                margin_top="16px",
            ),
            max_width="420px",
        ),
        open=CanvasState.import_dialog_open,
    )


def delete_confirm_dialog() -> rx.Component:
    return rx.alert_dialog.root(
        rx.alert_dialog.content(
            rx.alert_dialog.title("Delete Node?"),
            rx.alert_dialog.description(CanvasState.delete_message, size="2"),
            rx.flex(
                rx.el.button(
                    "Cancel",
                    on_click=CanvasState.cancel_delete,
                    class_name=_BTN,
                ),
                rx.el.button(
                    "Delete",
                    on_click=CanvasState.confirm_delete,
                    class_name=_DANGER_BTN,
                ),
                gap="8px",
                justify="end",
                margin_top="16px",
            ),
            max_width="420px",
        ),
        open=CanvasState.delete_target != "",
    )


def clear_confirm_dialog() -> rx.Component:
    return rx.alert_dialog.root(
        rx.alert_dialog.content(
            rx.alert_dialog.title("Rerun Node?"),
            rx.alert_dialog.description(
                "This deletes the node's response from the backend history "
                "and reruns its prompt. This cannot be undone.",
                size="2",
            ),
            rx.flex(
                rx.checkbox(
                    "Apply to descendants",
                    checked=CanvasState.clear_cascade,
                    on_change=CanvasState.set_clear_cascade,
                ),
                rx.text(
                    "Also clear and rerun every descendant node "
                    "(breadth-first), so their answers refresh too.",
                    class_name="text-xs text-dim pl-6",
                ),
                rx.checkbox(
                    "Include ancestors as context",
                    checked=CanvasState.clear_include_ancestors,
                    on_change=CanvasState.set_clear_include_ancestors,
                ),
                rx.text(
                    "Prefix each rerun prompt with its ancestor chain's "
                    "answers, as normal.",
                    class_name="text-xs text-dim pl-6",
                ),
                direction="column",
                gap="6px",
                margin_top="12px",
                margin_bottom="12px",
            ),
            rx.alert_dialog.description(
                CanvasState.clear_scope_message,
                size="2",
                class_name="font-medium text-default",
            ),
            rx.flex(
                rx.el.button(
                    "Cancel",
                    on_click=CanvasState.cancel_clear,
                    class_name=_BTN,
                ),
                rx.el.button(
                    "Rerun",
                    on_click=CanvasState.confirm_clear,
                    class_name=_DANGER_BTN,
                ),
                gap="8px",
                justify="end",
                margin_top="16px",
            ),
            max_width="440px",
        ),
        open=CanvasState.clear_target != "",
    )


def index() -> rx.Component:
    return rx.box(
        # Apply theme classes synchronously before components render (prevents flash).
        rx.script(compose_theme_init_script()),
        chat_canvas(
            nodes=CanvasState.nodes,
            edges=CanvasState.edges,
            mode=ThemeState.selected_mode,
            cascading_reruns=CASCADING_RERUNS,
            on_submit_prompt=CanvasState.submit_prompt,
            on_branch_node=CanvasState.branch_node,
            on_branch_from_text=CanvasState.branch_node_from_text,
            on_duplicate_node=CanvasState.duplicate_node,
            on_clear_node=CanvasState.request_clear,
            on_delete_node=CanvasState.request_delete,
            on_add_root=CanvasState.add_root,
            on_node_moved=CanvasState.node_moved,
            on_node_resized=CanvasState.node_resized,
            on_connect_edge=CanvasState.connect_edge,
            on_edge_reconnect=CanvasState.reconnect_edge,
            on_edge_delete=CanvasState.delete_edge,
            on_answer_question=CanvasState.answer_question,
        ),
        toolbar(),
        load_session_dialog(),
        import_session_dialog(),
        clear_confirm_dialog(),
        delete_confirm_dialog(),
        class_name="relative h-screen w-screen overflow-hidden bg-dark",
    )

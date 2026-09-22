# Copyright (c) 2026 Arvindra Sehmi
# Licensed under the MIT License — see LICENSE for details.

"""Reflex wrapper for the ChatCanvas ReactFlow component.

The canvas itself is a standalone React package —
packages/chat-tree-canvas-react — with no Reflex code in it; this app
consumes it like any other npm dependency (via a local file: spec) and is
just one possible host. All canvas data flows down as plain dicts (ReactFlow
node/edge shape) and all mutations flow back up as events — the JSX owns
nothing but transient UI state. Contract docs: the package README plus
docs/RENDERERS.md and docs/PROVIDERS.md.

Local-link setup (one-time per machine): bun's `file:` copies fail with
EPERM on Windows, so the package is consumed via bun's link protocol —
run `bun link` inside packages/chat-tree-canvas-react once (run.cmd does
this automatically), which registers the name globally; the `link:` spec
below then junctions it into .web/node_modules. Edits to the package source
are live immediately (junction + Vite /@fs), no sync step needed.
"""

import reflex as rx
from typing import Any, Dict, List


class ChatCanvas(rx.Component):
    # name@version form: Reflex installs the spec after the last "@" and
    # imports the name before it. Requires the one-time `bun link`
    # registration described above.
    library = "chat-tree-canvas-react@link:chat-tree-canvas-react"
    tag = "ChatCanvas"
    is_default = True

    # The package's runtime deps, installed into .web at the host level.
    # bun does NOT install a link:'d package's own dependencies, and Vite
    # resolves the linked package's bare imports against these host copies —
    # keep the versions in sync with packages/chat-tree-canvas-react/package.json.
    lib_dependencies: List[str] = [
        "reactflow@11.11.4",
        "react-markdown@9.0.1",
        "remark-gfm@4.0.0",
        "plotly.js-dist-min@2.35.2",
        "mermaid@10.9.4",
    ]

    def add_imports(self):
        # ReactFlow's stylesheet, imported host-side: CSS subpath imports
        # can't resolve from the linked package's real path (see the note in
        # the package's ChatCanvas.jsx).
        return {"": "reactflow/dist/style.css"}

    nodes: rx.Var[List[Dict[str, Any]]]
    edges: rx.Var[List[Dict[str, Any]]]
    # "light" | "dark" — selects the canvas palette (Performance theme).
    mode: rx.Var[str]
    # When true, duplicate/rerun buttons show on all nodes (not just leaves).
    # Mirrors the CASCADING_RERUNS env var — see app_rx/states/canvas_state.py.
    cascading_reruns: rx.Var[bool]

    on_submit_prompt: rx.EventHandler[lambda node_id, prompt: [node_id, prompt]]
    on_branch_node: rx.EventHandler[lambda node_id: [node_id]]
    on_branch_from_text: rx.EventHandler[lambda node_id, text: [node_id, text]]
    on_duplicate_node: rx.EventHandler[lambda node_id: [node_id]]
    on_clear_node: rx.EventHandler[lambda node_id: [node_id]]
    on_delete_node: rx.EventHandler[lambda node_id: [node_id]]
    on_add_root: rx.EventHandler[lambda x, y: [x, y]]
    on_node_moved: rx.EventHandler[lambda node_id, x, y: [node_id, x, y]]
    on_node_resized: rx.EventHandler[
        lambda node_id, x, y, width, height: [node_id, x, y, width, height]
    ]
    on_connect_edge: rx.EventHandler[lambda source, target: [source, target]]
    on_edge_reconnect: rx.EventHandler[
        lambda edge_id, source, target: [edge_id, source, target]
    ]
    on_edge_delete: rx.EventHandler[lambda edge_id: [edge_id]]
    on_answer_question: rx.EventHandler[
        lambda node_id, question_text, answer: [node_id, question_text, answer]
    ]


chat_canvas = ChatCanvas.create

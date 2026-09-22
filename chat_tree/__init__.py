# Copyright (c) 2026 Arvindra Sehmi
# Licensed under the MIT License — see LICENSE for details.

"""chat_tree — the framework-free core shared by every chat-tree host app.

Nothing in this package imports Reflex, Streamlit, or any other UI framework.
It holds everything a host needs to *mean* a conversation tree:

  providers/      backend contract + implementations (CHAT_API_PROVIDER)
  api_client      façade over the active provider
  blocks          stream-event → typed-display-block reducer (+ history replay)
  tree            pure graph algorithms: validation, BFS, tidy layout
  context         NodeContext/ConversationContext — builds the message sent
                  to a node's backend exchange (prompt first, ancestor
                  answers postfixed latest-first)
  models          node/edge dict factories (ReactFlow shape) + User/Role
  dag_store       SQLite persistence of tree *shape* (sessions/nodes/edges)
  exporter        chat-tree-canvas/v1 export/import format
  config          host policy flags read from the environment
  utils           small helpers (JWT decode, finite-number coercion)
  auth_cookies    starlette HTTP-only auth-cookie helpers (optional)
  storage         legacy pre-SQLite canvas.json import

Hosts (app_rx = Reflex, app_st = Streamlit) own UI state, wiring, and
scheduling; the visual canvas itself is the separate npm package
packages/chat-tree-canvas-react. See docs/PROVIDERS.md and docs/RENDERERS.md.
"""

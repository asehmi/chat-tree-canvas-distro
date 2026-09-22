# Copyright (c) 2026 Arvindra Sehmi
# Licensed under the MIT License — see LICENSE for details.

"""Session export/import in the shared intermediate JSON format.

Format "chat-tree-canvas/v1" — one file, two views of the same session, so
each app reads only the section it understands:

  {
    "format": "chat-tree-canvas/v1",
    "exported_at": "<iso8601 utc>",
    "session_id": "<canvas session>",
    "user_id": "<owner>",
    "canvas": {                      # exact tree shape — canvas importer
      "nodes": [{node_id, x, y, w, h, prompt}],
      "edges": [{edge_id, source, target}]
    },
    "conversations": [               # coalesced dialogues — backend importer
      {
        "conversation_id": "<sha256(session_id:leaf_node_id)[:32]>",
        "node_path": [node_id, ...],  # root → leaf
        "partial": bool,              # some node lacked an assistant answer
        "messages": [
          {"node_id", "role": "user", "text": <bare prompt>},
          {"node_id", "role": "assistant", ...raw backend history row...}
        ]
      }
    ]
  }

Terminology: a node keys one prompt/answer exchange; a *conversation* is a
full root-to-leaf depth-first path. Branch points duplicate their ancestry
across conversations on purpose — each path is a complete logical dialogue.
The user text is the bare prompt (NOT the context-prefixed message stored
backend-side): in a coalesced conversation the earlier messages ARE the
context, so embedding the prefix would duplicate every ancestor answer.

conversation_id is deterministic so re-importing the same file into a
downstream backend's own store can upsert instead of duplicating. node_id
is kept on every message, which makes the tree losslessly recoverable
from the coalesced form alone (shared node-path prefixes merge back into
a trie).

Backend mapping: one exported conversation maps to one round-numbered
conversation downstream, with the exchange at node_path[i] as round i.
"""

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

FORMAT = "chat-tree-canvas/v1"


def conversation_id(session_id: str, leaf_node_id: str) -> str:
    """Deterministic id for the root→leaf conversation ending at leaf_node_id."""
    return hashlib.sha256(f"{session_id}:{leaf_node_id}".encode()).hexdigest()[:32]


def dfs_paths(
    nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]]
) -> List[List[str]]:
    """All root→leaf paths (depth-first, edge insertion order).

    An isolated node is a one-element path. Nodes are trees (single parent,
    no cycles — enforced by CanvasState._validate_link), but the traversal
    guards against cycles anyway so a corrupt file can't hang the export.
    """
    ids = [n["id"] for n in nodes]
    known = set(ids)
    children: Dict[str, List[str]] = {i: [] for i in ids}
    has_parent = set()
    for e in edges:
        if e["source"] in known and e["target"] in known:
            children[e["source"]].append(e["target"])
            has_parent.add(e["target"])

    paths: List[List[str]] = []
    for root in ids:  # node insertion order keeps exports stable
        if root in has_parent:
            continue
        stack = [(root, [root])]
        while stack:
            current, path = stack.pop()
            kids = [c for c in children[current] if c not in path]
            if not kids:
                paths.append(path)
                continue
            for child in reversed(kids):  # reversed → DFS in insertion order
                stack.append((child, path + [child]))
    return paths


def build_export(
    session_id: str,
    user_id: str,
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
    histories: Dict[str, Optional[List[Dict[str, Any]]]],
) -> Dict[str, Any]:
    """Assemble the v1 export document.

    nodes/edges are the ReactFlow-shaped dicts from CanvasState. histories
    maps node_id → raw /chat/history rows (None = fetch failed/unavailable).
    """
    by_id = {n["id"]: n for n in nodes}

    conversations = []
    for path in dfs_paths(nodes, edges):
        messages: List[Dict[str, Any]] = []
        partial = False
        for nid in path:
            prompt = (by_id[nid]["data"].get("prompt") or "").strip()
            if not prompt:
                partial = True  # never-submitted node contributes nothing
                continue
            messages.append({"node_id": nid, "role": "user", "text": prompt})
            rows = histories.get(nid)
            assistant = next(
                (m for m in (rows or []) if m.get("role") == "assistant"), None
            )
            if assistant is None:
                partial = True  # user turn without an answer
            else:
                messages.append({**assistant, "node_id": nid, "role": "assistant"})
        if not messages:
            continue  # path of empty nodes — nothing to export
        conversations.append(
            {
                "conversation_id": conversation_id(session_id, path[-1]),
                "node_path": path,
                "partial": partial,
                "messages": messages,
            }
        )

    return {
        "format": FORMAT,
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "session_id": session_id,
        "user_id": user_id,
        "canvas": {
            "nodes": [
                {
                    "node_id": n["id"],
                    "x": n["position"]["x"],
                    "y": n["position"]["y"],
                    "w": n["data"].get("width", 0) or 0,
                    "h": n["data"].get("height", 0) or 0,
                    "prompt": n["data"].get("prompt", ""),
                }
                for n in nodes
            ],
            "edges": [
                {"edge_id": e["id"], "source": e["source"], "target": e["target"]}
                for e in edges
            ],
        },
        "conversations": conversations,
    }


def parse_import(raw: bytes | str) -> Dict[str, Any]:
    """Validate an uploaded export file and return its canvas section.

    Returns {"nodes": [...], "edges": [...]} (canvas rows as exported).
    Raises ValueError with a user-facing message on any problem.
    """
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"Not valid JSON: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("format") != FORMAT:
        raise ValueError(f'Not a "{FORMAT}" export file.')
    canvas = payload.get("canvas") or {}
    nodes = canvas.get("nodes") or []
    if not nodes:
        raise ValueError("Export contains no nodes.")
    for n in nodes:
        if not isinstance(n, dict) or not n.get("node_id"):
            raise ValueError("Malformed node entry in canvas section.")
    known = {n["node_id"] for n in nodes}
    edges = [
        e
        for e in (canvas.get("edges") or [])
        if isinstance(e, dict) and e.get("source") in known and e.get("target") in known
    ]
    return {"nodes": nodes, "edges": edges}

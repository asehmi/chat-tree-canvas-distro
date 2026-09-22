# Copyright (c) 2026 Arvindra Sehmi
# Licensed under the MIT License — see LICENSE for details.

"""Pure tree algorithms over ReactFlow-shaped node/edge dicts.

Every function takes plain lists of node dicts ({"id", "position", "data"})
and edge dicts ({"id", "source", "target"}) and mutates nothing — hosts keep
their own state (Reflex vars, st.session_state, …) and delegate the graph
math here. The tree invariant (single parent, no cycles) is enforced by
validate_link; traversals still carry cycle guards so corrupt data can't
hang a host.
"""

from typing import Any, Dict, List, Optional, Tuple

from chat_tree.models import NODE_WIDTH

Node = Dict[str, Any]
Edge = Dict[str, Any]


# ── basic relations ───────────────────────────────────────────

def node_index(nodes: List[Node], node_id: str) -> Optional[int]:
    for i, node in enumerate(nodes):
        if node["id"] == node_id:
            return i
    return None


def parent_of(edges: List[Edge], node_id: str, exclude_edge: str | None = None) -> Optional[str]:
    for edge in edges:
        if edge["target"] == node_id and edge["id"] != exclude_edge:
            return edge["source"]
    return None


def children_of(edges: List[Edge], node_id: str) -> List[str]:
    return [e["target"] for e in edges if e["source"] == node_id]


def refresh_leaves(nodes: List[Node], edges: List[Edge]) -> None:
    """Set data['leaf'] on every node in place: True iff it has no children.
    Call after any edge mutation, before persisting."""
    parents = {e["source"] for e in edges}
    for node in nodes:
        node["data"]["leaf"] = node["id"] not in parents


def is_ancestor(
    edges: List[Edge], maybe_ancestor: str, node_id: str, exclude_edge: str | None = None
) -> bool:
    current = parent_of(edges, node_id, exclude_edge)
    seen = set()
    while current is not None and current not in seen:
        if current == maybe_ancestor:
            return True
        seen.add(current)
        current = parent_of(edges, current)
    return False


def validate_link(
    nodes: List[Node],
    edges: List[Edge],
    source: str,
    target: str,
    exclude_edge: str | None = None,
) -> str:
    """'' if source→target is a legal tree edge, else a human-readable reason."""
    if source == target:
        return "A node can't be its own parent."
    if node_index(nodes, source) is None or node_index(nodes, target) is None:
        return "Unknown node."
    if parent_of(edges, target, exclude_edge) is not None:
        return "That node already has a parent — detach its existing edge first."
    if is_ancestor(edges, target, source, exclude_edge):
        return "That link would create a cycle."
    return ""


# ── traversals ────────────────────────────────────────────────

def subtree(edges: List[Edge], node_id: str) -> set:
    """The node and all its descendants (a set of ids)."""
    doomed = {node_id}
    frontier = [node_id]
    while frontier:
        current = frontier.pop()
        for child in children_of(edges, current):
            if child not in doomed:
                doomed.add(child)
                frontier.append(child)
    return doomed


def bfs_order(edges: List[Edge], node_id: str) -> List[str]:
    """Subtree node ids in breadth-first order, rooted at node_id."""
    order = [node_id]
    seen = {node_id}
    i = 0
    while i < len(order):
        for child in children_of(edges, order[i]):
            if child not in seen:
                seen.add(child)
                order.append(child)
        i += 1
    return order


# ── tidy layout ───────────────────────────────────────────────

def tidy_layout(
    nodes: List[Node],
    edges: List[Edge],
    h_gap: float = 60.0,      # between sibling subtrees
    root_gap: float = 120.0,  # between independent trees
    v_gap: float = 80.0,      # between depth rows
    est_h: float = 280.0,     # assumed height of auto-sized nodes
) -> Dict[str, Tuple[float, float]]:
    """Positions for a tidy tree: one row per depth, children centered under
    their parent, sibling subtrees packed left→right, independent trees side
    by side. Uses each node's actual width/height (data.width / data.height,
    0 = defaults). Anchored at the current layout's top-left corner so a
    host applying the result doesn't make the viewport jump.

    Returns {node_id: (x, y)} for every reachable node; unreachable nodes
    (corrupt edges) are omitted — hosts should leave those in place.
    """
    if not nodes:
        return {}

    by_id = {n["id"]: n for n in nodes}

    def width_of(nid: str) -> float:
        return by_id[nid]["data"].get("width") or NODE_WIDTH

    def height_of(nid: str) -> float:
        return by_id[nid]["data"].get("height") or est_h

    parents = {e["target"] for e in edges}
    roots = [n["id"] for n in nodes if n["id"] not in parents]

    # Post-order: each node's horizontal span = max(own width, packed
    # children spans). The seen-guard makes corrupt/cyclic edges harmless.
    span: Dict[str, float] = {}

    def measure(nid: str, seen: set) -> float:
        if nid in seen:
            return 0.0
        seen.add(nid)
        kids = children_of(edges, nid)
        group = sum(measure(k, seen) for k in kids) + h_gap * max(len(kids) - 1, 0)
        span[nid] = max(width_of(nid), group)
        return span[nid]

    seen: set = set()
    for root in roots:
        measure(root, seen)

    # Pre-order: node centered in its span, children group centered
    # beneath it; depth recorded for the row-Y pass.
    placed: Dict[str, Tuple[float, int]] = {}  # nid -> (x, depth)

    def place(nid: str, left: float, depth: int) -> None:
        if nid in placed:
            return
        placed[nid] = (left + (span[nid] - width_of(nid)) / 2, depth)
        kids = [k for k in children_of(edges, nid) if k in span]
        if not kids:
            return
        group = sum(span[k] for k in kids) + h_gap * (len(kids) - 1)
        cursor = left + (span[nid] - group) / 2
        for k in kids:
            place(k, cursor, depth + 1)
            cursor += span[k] + h_gap

    cursor = 0.0
    for root in roots:
        place(root, cursor, 0)
        cursor += span.get(root, NODE_WIDTH) + root_gap

    if not placed:
        return {}

    # Row Y offsets: each row clears the tallest node of the row above.
    max_h: Dict[int, float] = {}
    for nid, (_x, depth) in placed.items():
        max_h[depth] = max(max_h.get(depth, 0.0), height_of(nid))
    row_y: Dict[int, float] = {0: 0.0}
    for depth in range(1, (max(max_h) + 1) if max_h else 1):
        row_y[depth] = row_y[depth - 1] + max_h.get(depth - 1, est_h) + v_gap

    # Anchor the layout at the existing top-left corner.
    cur_min_x = min(n["position"]["x"] for n in nodes)
    cur_min_y = min(n["position"]["y"] for n in nodes)
    new_min_x = min(x for x, _d in placed.values())

    return {
        nid: (cur_min_x + (x - new_min_x), cur_min_y + row_y[depth])
        for nid, (x, depth) in placed.items()
    }

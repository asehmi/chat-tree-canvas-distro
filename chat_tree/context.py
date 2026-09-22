# Copyright (c) 2026 Arvindra Sehmi
# Licensed under the MIT License — see LICENSE for details.

"""Typed prompt/response context for building the message sent to a node's
backend exchange.

Replaces the old ad-hoc string concatenation in tree.build_context_prefix
(removed) with two small Pydantic models plus one explicit serialization
method — NodeContext pairs a node's prompt with its answer text;
ConversationContext bundles the node about to run with its ancestor chain
and knows how to flatten that into the one string the wire protocol
actually accepts.

Design notes:
- These are transient *view* models, built fresh from the plain ReactFlow-
  shaped node/edge dicts every time a message needs constructing — not a
  new storage format. Hosts still own nodes/edges as plain dicts (see
  tree.py's docstring); NodeContext.from_node_dict is the one adapter seam
  between the two. Rebuilding fresh per node (rather than mutating a shared
  NodeContext as a cascade recurses) avoids aliasing bugs across the BFS
  loop in rerun_subtree.
- The backend only ever accepts a flat message (chat_tree/providers), so
  to_prompt() still ends in string concatenation — the point of this module
  is to contain that to one clearly-named method instead of scattering it
  across tree.py and both hosts.
- Deliberately NOT the usual user/assistant turn format: by design
  decision, ancestor answers are appended AFTER the current prompt
  (postfix, not prefix), latest ancestor first. The current prompt always
  sits at the very top of the message so the backend's intent classifier
  reads it first, unobscured by history.
"""

from typing import List, Literal

from pydantic import BaseModel, Field

from chat_tree.blocks import answer_text
from chat_tree.tree import node_index, parent_of

CONTEXT_CHAR_BUDGET = 8000

_HISTORY_DELIMITER = "--- HISTORICAL CONTEXT (latest first) ---"
_ANSWER_SEPARATOR = "---"


class NodeContext(BaseModel):
    """One node's prompt/response pair, as context for building a message."""

    node_id: str
    prompt: str
    response: str = ""  # answer_text(blocks) — "" if not complete or no text
    status: Literal["idle", "streaming", "complete", "error"] = "idle"

    @classmethod
    def from_node_dict(cls, node: dict) -> "NodeContext":
        data = node["data"]
        return cls(
            node_id=node["id"],
            prompt=data.get("prompt", ""),
            response=answer_text(data.get("blocks", [])),
            status=data.get("status", "idle"),
        )

    @property
    def has_answer(self) -> bool:
        return self.status == "complete" and bool(self.response)


class ConversationContext(BaseModel):
    """The node about to run, plus its ancestor chain as historical context.

    `ancestors` is deepest-first (immediate parent, then grandparent, ...,
    root) — the order parent_of() naturally walks in, and the order
    to_prompt() serializes in. Only ancestors with a real answer are
    included (mirrors the old build_context_prefix's "complete" gate).
    """

    current: NodeContext
    ancestors: List[NodeContext] = Field(default_factory=list)

    @classmethod
    def build(
        cls,
        nodes: list,
        edges: list,
        node_id: str,
        current_prompt: str,
        include_ancestors: bool = True,
    ) -> "ConversationContext":
        current = NodeContext(node_id=node_id, prompt=current_prompt, status="streaming")

        ancestors: List[NodeContext] = []
        if include_ancestors:
            parent_id = parent_of(edges, node_id)
            while parent_id is not None:
                idx = node_index(nodes, parent_id)
                if idx is None:
                    break
                ctx = NodeContext.from_node_dict(nodes[idx])
                if ctx.has_answer:
                    ancestors.append(ctx)
                parent_id = parent_of(edges, parent_id)

        return cls(current=current, ancestors=ancestors)

    def to_prompt(self, budget: int = CONTEXT_CHAR_BUDGET) -> str:
        """The full message to send: current prompt first (so the intent
        classifier sees it unobscured), then — only if there's history — a
        delimiter and the ancestor answers, latest first, each separated by
        "---", truncated (oldest/root dropped first, since it's now at the
        tail) under `budget` chars.
        """
        if not self.ancestors:
            return self.current.prompt

        history = f"\n\n{_ANSWER_SEPARATOR}\n\n".join(a.response for a in self.ancestors)
        if len(history) > budget:
            history = history[:budget] + "…"

        return f"{self.current.prompt}\n\n{_HISTORY_DELIMITER}\n\n{history}"

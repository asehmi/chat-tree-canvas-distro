# Copyright (c) 2026 Arvindra Sehmi
# Licensed under the MIT License — see LICENSE for details.

"""Stream events → typed display blocks (the reducer every host shares).

Responses are stored as typed blocks (mirroring the provider's stream event
shapes) rather than one flattened markdown string:
  {"kind": "text",   "text": str}
  {"kind": "tool",   "tool": str, "status": "called|ok|denied", "reason": str}
  {"kind": "script", "code": str}
  {"kind": "metric", "label": str, "value": Any, "unit": str}
  {"kind": "table",  "title": str, "records": [dict]}
  {"kind": "chart",  "title": str, "figure": str}   # Plotly figure JSON
  {"kind": "mermaid", "title": str, "code": str}    # mermaid diagram source, rendered client-side
  {"kind": "question", "text": str}                 # pauses the turn for user input —
                                                      # see compose_followup_prompt and
                                                      # ChatCanvas.jsx's "answered" derivation

apply_event_to_blocks folds live stream events into that list, and
blocks_from_history replays persisted events through the same reducer — so
streaming and reload-from-history render identically by construction. The
matching front-end renderers live in packages/chat-tree-canvas-react
(dispatched on block "kind"); adding a new kind is documented in
docs/RENDERERS.md.
"""

from typing import Any, Dict, List


def _append_text(blocks: List[Dict[str, Any]], text: str) -> None:
    if not text:
        return
    if blocks and blocks[-1]["kind"] == "text":
        blocks[-1]["text"] += text
    else:
        blocks.append({"kind": "text", "text": text})


def apply_event_to_blocks(blocks: List[Dict[str, Any]], event: Dict[str, Any]) -> None:
    """Fold one SSE event into the block list (mutates `blocks`)."""
    etype = event.get("type")
    data = event.get("data", {}) or {}

    if etype == "text_delta":
        _append_text(blocks, data.get("delta", ""))

    elif etype == "tool_call":
        blocks.append(
            {"kind": "tool", "tool": data.get("tool", "tool"), "status": "called", "reason": ""}
        )

    elif etype == "tool_result":
        tool = data.get("tool")
        for block in reversed(blocks):
            if (
                block["kind"] == "tool"
                and block["status"] == "called"
                and (tool is None or block["tool"] == tool)
            ):
                block["status"] = "denied" if data.get("denied") else "ok"
                block["reason"] = data.get("reason", "")
                break

    elif etype == "sandbox_start":
        if data.get("code"):
            blocks.append({"kind": "script", "code": data["code"]})

    elif etype == "tool_call_result":
        kind = data.get("kind")
        if kind == "markdown":
            _append_text(blocks, "\n\n" + data.get("text", ""))
        elif kind == "metric":
            blocks.append(
                {
                    "kind": "metric",
                    "label": data.get("label", "Metric"),
                    "value": data.get("value", ""),
                    "unit": data.get("unit") or "",
                }
            )
        elif kind == "table":
            blocks.append(
                {
                    "kind": "table",
                    "title": data.get("title", ""),
                    "records": data.get("records", []),
                }
            )
        elif kind == "chart":
            blocks.append(
                {
                    "kind": "chart",
                    "title": data.get("title", ""),
                    "figure": data.get("figure_json", ""),
                }
            )
        elif kind == "mermaid":
            blocks.append(
                {
                    "kind": "mermaid",
                    "title": data.get("title", ""),
                    "code": data.get("code", ""),
                }
            )
        elif kind == "question":
            blocks.append({"kind": "question", "text": data.get("text", "")})

    elif etype == "result":
        # full_analysis delivers its report + charts here, not as deltas
        report = data.get("final_report", "")
        existing = "".join(b.get("text", "") for b in blocks if b["kind"] == "text")
        if report and report not in existing:
            _append_text(blocks, ("\n\n" if blocks else "") + report)
        for chart in data.get("charts", []) or []:
            if chart.get("json"):
                blocks.append(
                    {
                        "kind": "chart",
                        "title": chart.get("type", "chart"),
                        "figure": chart["json"],
                    }
                )


def answer_text(blocks: List[Dict[str, Any]]) -> str:
    """Markdown/plain-text content of an answer, for chat_tree.context's
    NodeContext.response.

    Tables and charts are redacted, replaced by a note (column names for
    tables, title for charts) so downstream turns know they existed.
    """
    parts: List[str] = []
    for block in blocks:
        if block["kind"] == "text" and block["text"].strip():
            parts.append(block["text"].strip())
        elif block["kind"] == "table":
            records = block.get("records") or []
            cols = ", ".join(str(c) for c in records[0].keys()) if records else "none"
            title = block.get("title") or "untitled"
            parts.append(f'[table "{title}" removed from context — columns: {cols}]')
        elif block["kind"] == "chart":
            title = block.get("title") or "untitled"
            parts.append(f'[chart "{title}" removed from context]')
        elif block["kind"] == "mermaid":
            title = block.get("title") or "untitled"
            parts.append(f'[mermaid diagram "{title}" removed from context]')
        elif block["kind"] == "question" and block["text"].strip():
            parts.append(f'[Agent asked: "{block["text"].strip()}"]')
    return "\n".join(parts).strip()


def compose_followup_prompt(question: str, answer: str) -> str:
    """The prompt a Q&A follow-up child node is created with — identical
    across both hosts so the "already answered" check in ChatCanvas.jsx
    (matching a question's text against child prompts) works regardless of
    which host created the child."""
    return f"Agent asked: {question.strip()}. User responded with: {answer.strip()}."


def blocks_from_history(msg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Rebuild response blocks from a persisted assistant history row by
    replaying its stored SSE events through the reducer."""
    blocks: List[Dict[str, Any]] = []
    for event in msg.get("events", []) or []:
        apply_event_to_blocks(blocks, event)
    result = msg.get("result") or {}
    if result:
        apply_event_to_blocks(blocks, {"type": "result", "data": result})
    if not blocks and msg.get("text"):
        _append_text(blocks, msg["text"])
    return blocks


def missing_content_warning(count: int) -> str:
    """User-facing warning after hydration found nodes without backend content."""
    if count == 1:
        return (
            "1 node had no content on the backend — its prompt is kept; "
            "rerun it to generate a fresh response."
        )
    return (
        f"{count} nodes had no content on the backend — their prompts are "
        "kept; rerun them to generate fresh responses."
    )

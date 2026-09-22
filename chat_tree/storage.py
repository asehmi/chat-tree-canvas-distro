# Copyright (c) 2026 Arvindra Sehmi
# Licensed under the MIT License — see LICENSE for details.

"""Legacy JSON canvas support.

The canvas now persists its shape to SQLite (chat_tree/dag_store.py) and
re-hydrates content from the backend's /chat/history API. This module only
remains to import a pre-SQLite data/canvas.json into the first session, once.
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

# chat_tree/ sits directly under the repo root: two parents, not three.
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CANVAS_FILE = DATA_DIR / "canvas.json"


def load_legacy_canvas() -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Load the old single-canvas JSON file, or ([], []) if absent/corrupt."""
    if not CANVAS_FILE.exists():
        return [], []
    try:
        raw = json.loads(CANVAS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return [], []
    nodes = raw.get("nodes", [])
    edges = raw.get("edges", [])
    for node in nodes:
        data = node.setdefault("data", {})
        # Migrate pre-blocks canvases (single flattened "response" string).
        if "blocks" not in data:
            response = data.pop("response", "")
            data["blocks"] = [{"kind": "text", "text": response}] if response else []
        data.setdefault("round", 0)
        data.setdefault("leaf", True)
        data.setdefault("error", "")
        data.setdefault("route", "")
        if data.get("status") == "streaming":
            data["status"] = "complete" if data.get("blocks") else "error"
            if not data.get("blocks"):
                data["error"] = "Interrupted — the app closed while streaming."
    return nodes, edges


def mark_legacy_imported() -> None:
    if CANVAS_FILE.exists():
        CANVAS_FILE.rename(CANVAS_FILE.with_suffix(".json.imported"))

# Copyright (c) 2026 Arvindra Sehmi
# Licensed under the MIT License — see LICENSE for details.

"""Shared return shape for every tool executor in this provider.

One dataclass so `insecure_agent_provider.py`'s tool dispatch can treat
`run_python` and every market-data tool identically: push `block_events`
onto the SSE queue, feed `summary_text` back to the model as the tool
result. `insecure_agent/script_runner.py`'s `ScriptResult` is this same
shape under its original name (kept for that module's own readability).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolResult:
    block_events: list[dict[str, Any]] = field(default_factory=list)
    summary_text: str = ""

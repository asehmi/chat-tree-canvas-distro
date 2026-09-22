# Copyright (c) 2026 Arvindra Sehmi
# Licensed under the MIT License — see LICENSE for details.

"""Unsandboxed Python execution for the insecure agent provider.

*** THIS RUNS MODEL-WRITTEN CODE WITH NO SANDBOXING WHATSOEVER. ***
Full filesystem, network, and process access, as whatever account is
running this server. Only run this on a machine you fully trust, never
exposed to a network, never anywhere it can reach anything sensitive — see
the root README.md's safety section before running this at all.

`run_python()` writes the given code to a temp `.py` file, `subprocess.run`s
it, and parses stdout for a small `#RESULT_*` marker convention (this
project's own — see `insecure_agent/llm/tools.py`'s `TOOL_DEFINITIONS`,
which is what actually tells the model this convention exists) into the
typed block-event shapes `chat_tree/blocks.py` already knows how to render.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from insecure_agent.tool_result import ToolResult as ScriptResult

TIMEOUT_SECONDS = 20
MAX_OUTPUT_CHARS = 4000  # trimmed before being fed back to the model as the tool result

_MARKERS = {
    "#RESULT_METRIC": "metric",
    "#RESULT_TABLE": "table",
    "#RESULT_CHART": "chart",
    "#RESULT_MERMAID": "mermaid",
}


def run_python(code: str, timeout: int = TIMEOUT_SECONDS) -> ScriptResult:
    """Run `code` unsandboxed and parse its stdout.

    Any `#RESULT_*` marker line becomes one `tool_call_result` event.
    Every other stdout line (plus stderr, if the run failed) becomes the
    plain-text summary handed back to the model as the tool's result, so
    it can narrate what happened.
    """
    with tempfile.TemporaryDirectory(prefix="insecure_agent_run_") as tmp_dir:
        script_path = Path(tmp_dir) / "script.py"
        script_path.write_text(code, encoding="utf-8")

        try:
            proc = subprocess.run(
                [sys.executable, str(script_path)],
                cwd=tmp_dir,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return ScriptResult(
                block_events=[],
                summary_text=f"Script timed out after {timeout}s and was killed.",
            )

        block_events: list[dict[str, Any]] = []
        text_lines: list[str] = []
        for line in proc.stdout.splitlines():
            marker, _, payload = line.partition(" ")
            kind = _MARKERS.get(marker)
            if kind is None:
                text_lines.append(line)
                continue
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                # Malformed marker line — surface it as plain text rather
                # than silently dropping it, so the failure is visible.
                text_lines.append(line)
                continue
            data["kind"] = kind
            block_events.append({"type": "tool_call_result", "data": data})

        stdout_text = "\n".join(text_lines).strip()
        summary_parts: list[str] = []
        if proc.returncode != 0:
            summary_parts.append(f"Script exited with code {proc.returncode}.")
        if stdout_text:
            summary_parts.append(f"stdout:\n{stdout_text[:MAX_OUTPUT_CHARS]}")
        if proc.stderr.strip():
            summary_parts.append(f"stderr:\n{proc.stderr.strip()[:MAX_OUTPUT_CHARS]}")
        if block_events:
            kinds = ", ".join(e["data"]["kind"] for e in block_events)
            summary_parts.append(f"Produced {len(block_events)} structured result(s): {kinds}")
        if not summary_parts:
            summary_parts.append("Script ran with no output.")

        return ScriptResult(block_events=block_events, summary_text="\n\n".join(summary_parts))

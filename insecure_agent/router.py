# Copyright (c) 2026 Arvindra Sehmi
# Licensed under the MIT License — see LICENSE for details.

"""Router — decide whether a message is a script-writing request before the
main turn even starts, and (for that case) generate the script as plain
text instead of tool-calling.

A router classifies intent FIRST (one dedicated call), and only then asks
the model, with a single-purpose prompt, for nothing but a fenced
```python block. There's no ambiguity left for the model to resolve by the
time it's asked to write code. Without this, run_python has to compete for
attention as one of several tools in a single flat agentic loop, which is
far less reliable at getting invoked for an explicit "write me a script"
request — this module fixes that by giving script-writing its own
dedicated treatment.

insecure_agent's run_python is deliberately unsandboxed (no Docker, no
import scanning, no resource caps), has no bridge/tool_client concept for
calling other tools from inside a generated script, and keeps its own
#RESULT_* convention for structured output — see
insecure_agent/llm/tools.py's RUN_PYTHON_RESULT_CONVENTION, reused
verbatim below so the two call paths can't drift apart.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Literal

from insecure_agent.llm import get_llm_client
from insecure_agent.llm.tools import RUN_PYTHON_RESULT_CONVENTION

logger = logging.getLogger("insecure_agent.router")

Mode = Literal["custom_script", "agent_loop"]

_ROUTER_SYSTEM_PROMPT = (
    "You are an intent classifier for a financial-analyst chat agent. "
    "Classify the user's message into exactly one of two modes.\n\n"
    "\"custom_script\" — the user asks for a SCRIPT, PROGRAM, or CODE as the "
    "deliverable, in any phrasing: \"write/build/create/make a script/program "
    "to...\", \"run Python to...\", \"calculate using code...\", \"execute...\", "
    "or anything else where the user names the form of the answer as code. "
    "This is about the FORM the user asked for, not the topic — a request "
    "like \"build a script to fetch AAPL data and analyse it\" is "
    "custom_script even though it's *about* financial analysis, because the "
    "user explicitly asked for a script. When the message names \"script\", "
    "\"program\", or \"code\" as what it wants produced, that always wins over "
    "any topic overlap with agent_loop's own tools below.\n\n"
    "\"agent_loop\" — everything else: financial analysis questions, ticker "
    "lookups, news search, general questions, or anything that does not "
    "itself ask for a script/program/code as the deliverable.\n\n"
    "Respond with your classification."
)

_ROUTER_SCHEMA = {
    "type": "object",
    "properties": {
        "mode": {"type": "string", "enum": ["custom_script", "agent_loop"]},
    },
    "required": ["mode"],
}

_SCRIPT_OVERRIDE_RE = re.compile(r"^/script\b\s*", re.IGNORECASE)

_FENCE_RE = re.compile(r"```(?:[Pp]ython)?[ \t]*\r?\n(.*?)```", re.DOTALL)
_OPEN_FENCE_RE = re.compile(r"```(?:[Pp]ython)?[ \t]*\r?\n(.*)$", re.DOTALL)


def classify_intent(message: str) -> Mode:
    """One dedicated classification call. Falls back to "agent_loop" on any
    failure (bad JSON, LLM error) — a classification hiccup must never
    break a turn that would otherwise have worked fine via the normal
    tool-calling path."""
    try:
        result = get_llm_client().generate_response(
            _ROUTER_SYSTEM_PROMPT, message, response_schema=_ROUTER_SCHEMA
        )
        mode = result.get("mode") if isinstance(result, dict) else None
        if mode in ("custom_script", "agent_loop"):
            return mode
        logger.warning(f"classify_intent: unexpected response {result!r}, defaulting to agent_loop")
    except Exception as exc:
        logger.warning(f"classify_intent failed ({exc}), defaulting to agent_loop")
    return "agent_loop"


def strip_script_override(message: str) -> tuple[bool, str]:
    """A leading '/script' forces custom_script mode and skips
    classify_intent entirely. Only this one prefix exists here; there are
    no other mode-override prefixes needed, since every other mode is
    already just an ordinary tool call."""
    stripped = message.strip()
    match = _SCRIPT_OVERRIDE_RE.match(stripped)
    if not match:
        return False, message
    return True, stripped[match.end():]


def _build_script_system_prompt() -> str:
    return (
        "You are a Python programming assistant. Generate a complete, "
        "self-contained Python script that accomplishes the task described.\n\n"
        "Respond with ONLY a single fenced code block — no preamble, "
        "explanation, or commentary before or after it (\"I'll help you...\" "
        "etc.). Your entire response must be parseable as: optional "
        "whitespace, then a ```python fence, then the script, then the "
        "closing ``` fence, then nothing else.\n\n"
        "Output plain text/progress using print() as normal — it will be "
        f"captured and shown to the user as-is. {RUN_PYTHON_RESULT_CONVENTION}\n\n"
        "This script runs with no sandboxing and the same permissions as "
        "the server process — never write, delete, or exfiltrate anything "
        "you weren't explicitly asked to, and never do anything "
        "network-facing unless the task specifically calls for it.\n\n"
        "Wrap the script in a single ```python ... ``` code block."
    )


def generate_script(task_description: str) -> tuple[str | None, str | None]:
    """Returns (code, None) on success or (None, error_message) on
    failure. Calls the LLM as plain text completion — deliberately no
    `tools=` argument: the model's entire job here is producing a code
    fence, not choosing between tools."""
    raw = str(get_llm_client().generate_response(_build_script_system_prompt(), task_description))
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()

    match = _FENCE_RE.search(raw)
    if match is not None:
        return match.group(1).strip(), None

    if _OPEN_FENCE_RE.search(raw):
        logger.warning(f"generate_script: fence opened but never closed — task={task_description!r} raw_tail={raw[-300:]!r}")
        return None, (
            "The generated script was cut off before it finished (likely too long "
            "for the response limit). Try asking for something narrower, or "
            "simplify the request."
        )

    logger.warning(f"generate_script: no python fence found at all — task={task_description!r} raw={raw[:1000]!r}")
    return None, "The model didn't return a fenced ```python code block, so nothing was executed. Try rephrasing the request."

# Copyright (c) 2026 Arvindra Sehmi
# Licensed under the MIT License — see LICENSE for details.

"""Tool-calling primitives shared by every provider client, plus this
provider's one tool.

`ToolCall` and `ToolCallLoopGuard` are provider-agnostic (ported verbatim
from a sibling project's `lib/llm/tools.py` — they never touched anything
project-specific there either). `TOOL_DEFINITIONS` and the dispatch in
`insecure_agent_provider.py` are new: one tool, `run_python`, backed by
`insecure_agent/script_runner.py`.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel


class ToolCall(BaseModel):
    """One tool invocation requested by a model — provider-agnostic, built
    fresh by each provider client from whatever shape that provider's API
    returns (Anthropic's `tool_use` block, Gemini's `function_call` part,
    OpenRouter's streamed OpenAI-style tool call)."""

    id: str
    name: str
    arguments: dict[str, Any]


class EndTurn(Exception):
    """Raised by insecure_agent_provider.py's tool_handler after `ask_user`
    runs, to make each client's generate_response_stream stop calling the
    LLM further and end the round right after the question block — a
    question pauses the turn for a follow-up answer, so nothing should
    follow it in the same stream. Each of anthropic_client.py/
    gemini_client.py/openrouter_client.py catches this around its
    tool_handler(call) invocation inside generate_response_stream (only —
    generate_response's non-streaming tool loop is untouched, since
    insecure_agent_provider.py never calls it) and returns immediately."""


class ToolCallLoopGuard:
    """Detects a stuck agentic tool-call loop and raises before it can run
    away. Two independent triggers:
    - **Too many rounds total** (`max_rounds`) — a blunt backstop.
    - **The same tool call (name + arguments) repeated back to back**
      (`max_consecutive_repeats`) — a sharper signal that something's
      actually stuck, not just a legitimately multi-step task taking a few
      rounds.
    """

    def __init__(self, max_rounds: int = 8, max_consecutive_repeats: int = 2):
        self.max_rounds = max_rounds
        self.max_consecutive_repeats = max_consecutive_repeats
        self._rounds = 0
        self._last_signature: str | None = None
        self._repeat_count = 0

    def check(self, calls: list[ToolCall]) -> None:
        """Call once per round, with that round's requested tool calls,
        before executing any of them. Raises `RuntimeError` if the loop
        looks stuck; otherwise returns normally."""
        self._rounds += 1
        if self._rounds > self.max_rounds:
            raise RuntimeError(
                f"Tool-call loop exceeded {self.max_rounds} rounds without "
                "converging"
            )

        signature = "|".join(
            sorted(
                f"{c.name}:{json.dumps(c.arguments, sort_keys=True)}"
                for c in calls
            )
        )
        if signature == self._last_signature:
            self._repeat_count += 1
        else:
            self._last_signature = signature
            self._repeat_count = 1

        if self._repeat_count > self.max_consecutive_repeats:
            raise RuntimeError(
                f"Same tool call(s) repeated {self._repeat_count} times in "
                f"a row, treating as a stuck loop: {signature}"
            )


# The #RESULT_* convention is this project's own (not shared with, or
# copied from, any other backend) — see insecure_agent/script_runner.py for
# the parser this text has to stay in sync with. Shared by the run_python
# tool schema below AND insecure_agent/router.py's dedicated script-writing
# prompt (the custom_script path), so both call paths teach the model the
# exact same convention and can't drift apart.
RUN_PYTHON_RESULT_CONVENTION = (
    "To return a structured result the canvas can render, print ONE line "
    "starting with one of these markers, followed by a single JSON object "
    "(all on that one line):\n"
    "  #RESULT_METRIC {\"label\": str, \"value\": number|str, \"unit\": str}\n"
    "  #RESULT_TABLE {\"title\": str, \"records\": [ {col: value, ...}, ... ]}\n"
    "  #RESULT_CHART {\"title\": str, \"figure_json\": \"<a Plotly figure, JSON-encoded as a string>\"}\n"
    "  #RESULT_MERMAID {\"title\": str, \"code\": \"<mermaid diagram source>\"}\n"
    "You may print several such lines to return several results."
)

# Provider-neutral tool definition: name, description, and a JSON-schema
# `parameters` shape. Every provider client reshapes this into its own wire
# format before calling out (see each client module's own adapter
# function) — Anthropic needs the schema under `input_schema` specifically,
# Gemini and OpenRouter need their own small key renames.
TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "run_python",
        "description": (
            "Run Python to compute, analyze, or visualize something. "
            "Whatever it prints to stdout is captured. Plain printed text "
            "becomes prose in your answer. "
            f"{RUN_PYTHON_RESULT_CONVENTION} "
            "This code runs with no sandboxing and the same permissions as "
            "the server process — never write, delete, or exfiltrate "
            "anything you weren't explicitly asked to."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python source to run."},
            },
            "required": ["code"],
        },
    },
    {
        "name": "validate_ticker",
        "description": "Validate and extract a stock ticker symbol from free text or an explicit symbol.",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Free-text query containing a company name or ticker, OR a bare ticker symbol like 'AAPL'.",
                },
            },
            "required": ["text"],
        },
    },
    {
        "name": "run_full_analysis",
        "description": (
            "The financial-analyst pipeline, atomically: fetches OHLCV market data, "
            "computes technical indicators (RSI, MACD, SMA, ATR, Bollinger Bands), "
            "writes a quantitative 'Market Data Analysis' report, renders all three "
            "Plotly charts (stock price, RSI/MACD, volume), then writes a strategic "
            "'Market Insights' report addressing the user's actual question. One call "
            "does the whole thing server-side — there is no separate scrape/compute/"
            "chart step to call, and no data to copy between calls."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "Stock ticker symbol or company name (e.g. 'AAPL' or 'Apple') — resolved automatically.",
                },
                "period": {
                    "type": "string",
                    "description": (
                        "Lookback period. One of: 1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 3y, 5y, "
                        "10y, max. Use at least 6mo (default 1y) even if the user's "
                        "question only asks about recent activity — the data report "
                        "needs >=90 rows of history and will note when it has too little; "
                        "fetching more history than the question needs is always safe, "
                        "fetching too little is not."
                    ),
                    "default": "1y",
                },
                "user_prompt": {
                    "type": "string",
                    "description": "The user's actual question, to condition the strategic report. Defaults to a generic prompt if omitted.",
                    "default": "",
                },
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "search",
        "description": "Search for recent news and information about a stock or financial topic.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query — a ticker symbol, company name, or financial topic."},
                "max_results": {"type": "integer", "description": "Maximum number of results to return.", "default": 5},
            },
            "required": ["query"],
        },
    },
    {
        "name": "ask_user",
        "description": (
            "Ask the user a single clarifying question when genuinely blocked on a "
            "required input that cannot be inferred or defaulted. Ends the turn with "
            "the question — do not call this for confirmations, approvals, or yes/no "
            "preferences, and never call any other tool or say anything further after it."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "The single clarifying question to ask the user."},
            },
            "required": ["question"],
        },
    },
]

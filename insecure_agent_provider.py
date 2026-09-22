# Copyright (c) 2026 Arvindra Sehmi
# Licensed under the MIT License — see LICENSE for details.

"""insecure_agent_provider — a REAL, reference-protocol chat backend for
chat-tree-canvas: real LLM calls, real tool calling, and real generated
charts/tables/mermaid diagrams — not the canned demo `mock_provider.py`
gives you.

*** READ THIS BEFORE RUNNING IT ***
This agent has a real financial-analyst toolset (ticker validation, market
data + technical indicators, LLM-written analysis reports, Plotly charts,
news search, clarifying questions — see `insecure_agent/market_tools.py`)
plus `run_python` (see `insecure_agent/llm/tools.py`), which executes
model-written Python with **no sandboxing whatsoever**
(`insecure_agent/script_runner.py`): full filesystem, network, and process
access, as whatever account runs this server. This is a deliberate
trade-off for a giveaway that has to run anywhere without a container
runtime, not an oversight. Only run this:
  - on a machine you fully trust, with nothing sensitive it could reach
  - never exposed to a network — bind to localhost only, never behind a
    public URL or port-forward
  - as a throwaway local demo, never in anything resembling production
See the root README.md's safety section for the full picture.

Run it:
    .venv\\Scripts\\uvicorn insecure_agent_provider:app --port 9000

Point the canvas at it (.env), then restart `reflex run` / the Streamlit app:
    CHAT_API_PROVIDER=reference
    CHAT_API_BASE_URL=http://localhost:9000
    LLM_PROVIDER=anthropic            # or gemini / openrouter
    ANTHROPIC_API_KEY=...             # whichever provider's key you set

Chat history persists to a local SQLite file (`.db/insecure_agent_chat.db`,
via `insecure_agent/db.py`), so a restart no longer clears history the way
`mock_provider.py`'s in-memory dict does. Only session tokens stay
in-memory (ephemeral by design — the canvas already re-mints its token on
a 401, so a server restart is transparent to the user).
"""

from __future__ import annotations

import asyncio
import json
import os
import queue
import threading
import uuid
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Any, Dict, List

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

# Unlike app_st/app.py and rxconfig.py, this server is normally launched
# standalone (`uvicorn insecure_agent_provider:app`), not via reflex/streamlit
# — neither of which auto-loads .env for it. Without this call, LLM_PROVIDER/
# *_API_KEY are only picked up if already exported in the shell.
load_dotenv()

from insecure_agent import db, market_tools  # noqa: E402
from insecure_agent.llm import get_llm_client  # noqa: E402
from insecure_agent.llm.tools import TOOL_DEFINITIONS, EndTurn, ToolCall  # noqa: E402
from insecure_agent.router import classify_intent, generate_script, strip_script_override  # noqa: E402
from insecure_agent.script_runner import run_python  # noqa: E402
from insecure_agent.tool_result import ToolResult  # noqa: E402

# Every tool takes its call's `arguments` dict and returns a ToolResult.
# `run_python` is kept separate from market_tools.py (it's the one tool
# with no financial-analyst dependency, and its `sandbox_start` SSE event
# below is unique to it), everything else dispatches straight into
# insecure_agent/market_tools.py.
TOOL_REGISTRY: dict[str, "Callable[[dict], ToolResult]"] = {
    "run_python": lambda a: run_python(a.get("code", "")),
    "validate_ticker": lambda a: market_tools.validate_ticker(a.get("text", "")),
    "run_full_analysis": lambda a: market_tools.run_full_analysis(
        a.get("ticker", ""), a.get("period", "1y"), a.get("user_prompt", "")
    ),
    "search": lambda a: market_tools.search(a.get("query", ""), a.get("max_results", 5)),
    "ask_user": lambda a: market_tools.ask_user(a.get("question", "")),
}


def _safe_truncate(value: str | None, prefix_len: int = 5, suffix_len: int = 5) -> str:
    """Show enough of a secret to confirm it's set/correct, never the whole thing."""
    if not value:
        return "<not set>"
    if len(value) < prefix_len + suffix_len + 3:
        return value
    return value[:prefix_len] + "..." + value[-suffix_len:]


def _print_env_vars() -> None:
    """Print what this process actually sees for the vars it reads, so a
    mismatch between .env and the running server is obvious at startup
    instead of surfacing later as a confusing auth/provider error."""
    print(
        "| insecure_agent_provider: ENVIRONMENT VARIABLES LOADED |",
        "\n| Provider:",
        os.getenv("LLM_PROVIDER", "<not set, defaults to anthropic>"),
        "\n| Anthropic:",
        _safe_truncate(os.getenv("ANTHROPIC_API_KEY")),
        os.getenv("ANTHROPIC_MODEL", "<using client default>"),
        "\n| Gemini:",
        _safe_truncate(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")),
        os.getenv("GEMINI_MODEL", "<using client default>"),
        "\n| OpenRouter:",
        _safe_truncate(os.getenv("OPENROUTER_API_KEY") or os.getenv("OR_API_KEY")),
        os.getenv("OPENROUTER_MODEL", "<using client default>"),
        "|",
    )


@asynccontextmanager
async def _lifespan(app: FastAPI):
    _print_env_vars()
    await db.init()
    try:
        yield
    finally:
        await db.close()


app = FastAPI(
    title="chat-tree-canvas insecure agent provider", version="1.0", lifespan=_lifespan
)

SESSIONS: Dict[str, str] = {}  # token -> user_id

SYSTEM_PROMPT = (
    "You are the demo agent behind chat-tree-canvas's insecure_agent_provider "
    "— a giveaway reference-protocol backend, not the project's real agent "
    "service. Answer the user's message directly and helpfully.\n\n"
    "You have a financial-analyst toolset: validate_ticker (resolve a company "
    "name or symbol on its own, if you just need to check one), "
    "run_full_analysis (the real workhorse — one call fetches market data, "
    "computes technical indicators, writes a data-driven report, renders "
    "stock/RSI-MACD/volume charts, and writes a strategic report addressing "
    "the user's question, all server-side; always use a period of at least "
    "6mo, default 1y, even if the question only asks about recent activity), "
    "search (news and background on a stock or topic), and ask_user (a "
    "single clarifying question when genuinely blocked — this ends your "
    "turn immediately, so never call it alongside or before other tools in "
    "the same turn, and never say anything more after calling it).\n\n"
    "You also have run_python, for anything else that benefits from real "
    "computation. Its own description explains exactly how to return "
    "structured results (metric/table/chart/mermaid) instead of plain text. "
    "If you are about to write actual Python code as part of your answer, "
    "call run_python with that code instead of just showing it as text — "
    "never print/describe a script without running it, the user cannot "
    "execute code you only display.\n\n"
    "This is a local demo running unsandboxed code on the reader's own "
    "machine: never write, delete, or read anything outside the obvious "
    "scope of what was asked, and never do anything network-facing unless "
    "the user's request specifically calls for it."
)


def _require_session(token: str | None) -> str:
    if not token or token not in SESSIONS:
        raise HTTPException(status_code=401, detail="Invalid or missing session token")
    return SESSIONS[token]


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "provider": "insecure_agent"}


@app.post("/session")
async def mint_session(request: Request) -> dict:
    body = await request.json()
    user_id = (body.get("user_id") or "anonymous").strip() or "anonymous"
    token = uuid.uuid4().hex
    SESSIONS[token] = user_id
    return {"session_token": token}


@app.get("/history/{node_id}")
async def get_history(node_id: str, x_session_token: str | None = Header(None)) -> dict:
    _require_session(x_session_token)
    return {"messages": await db.load_history(node_id)}


@app.delete("/history/{node_id}/{round_num}")
async def delete_round(
    node_id: str, round_num: int, x_session_token: str | None = Header(None)
) -> dict:
    _require_session(x_session_token)
    await db.delete_round(node_id, round_num)
    return {"deleted": round_num}


def _run_agent(q: "queue.Queue[dict | None]", message: str, round_num: int) -> None:
    """Runs on a background thread — every provider client's
    generate_response_stream() is a plain synchronous generator (the
    ported SDKs are all sync), and the tool_handler it calls mid-stream is
    a sync callback too. Pushing events into a thread-safe queue here, and
    draining it from the async /chat handler via run_in_executor, is the
    same bridge app_st/controller.py's _stream_worker/_StreamBox already
    uses for the same sync-generator-inside-an-async-app problem.

    Two paths, chosen before anything else runs (see insecure_agent/router.py):
    - "custom_script": a leading /script forces it, otherwise one dedicated
      classification call decides. The model is asked for a plain-text
      ```python fence (no tool-calling at all) and it's executed directly —
      this is what makes explicit "write a script to..." requests reliable,
      instead of competing with four other tools in the agent loop below.
    - "agent_loop": the existing tool-calling loop, unchanged — run_python
      stays available here too, for an inline calculation that doesn't
      warrant a whole dedicated turn.
    """
    forced, message = strip_script_override(message)
    mode = "custom_script" if forced else classify_intent(message)

    if mode == "custom_script":
        q.put({"type": "route_decision", "data": {"mode": "custom_script"}})
        try:
            code, error = generate_script(message)
            if error:
                q.put({"type": "error", "data": {"message": error}})
            else:
                q.put({"type": "sandbox_start", "data": {"code": code}})
                result = run_python(code)
                for block_event in result.block_events:
                    q.put(block_event)
                q.put({
                    "type": "tool_result",
                    "data": {"tool": "run_python", "denied": False, "reason": ""},
                })
        except Exception as exc:  # noqa: BLE001
            q.put({"type": "error", "data": {"message": str(exc)}})
        q.put({"type": "done", "data": {"round": round_num}})
        q.put(None)
        return

    q.put({"type": "route_decision", "data": {"mode": "insecure_agent"}})

    def tool_handler(call: ToolCall) -> str:
        q.put({"type": "tool_call", "data": {"tool": call.name}})
        executor = TOOL_REGISTRY.get(call.name)
        if executor is None:
            q.put({
                "type": "tool_result",
                "data": {"tool": call.name, "denied": True, "reason": "unknown tool"},
            })
            return f"Unknown tool: {call.name}"

        if call.name == "run_python":
            q.put({"type": "sandbox_start", "data": {"code": call.arguments.get("code", "")}})

        result = executor(call.arguments)
        for block_event in result.block_events:
            q.put(block_event)
        q.put({
            "type": "tool_result",
            "data": {"tool": call.name, "denied": False, "reason": ""},
        })

        if call.name == "ask_user":
            raise EndTurn()
        return result.summary_text

    try:
        client = get_llm_client()
        for chunk in client.generate_response_stream(
            SYSTEM_PROMPT, message, tools=TOOL_DEFINITIONS, tool_handler=tool_handler
        ):
            q.put({"type": "text_delta", "data": {"delta": chunk}})
    except Exception as exc:  # noqa: BLE001 — surface anything on the node, not just known types
        q.put({"type": "error", "data": {"message": str(exc)}})

    q.put({"type": "done", "data": {"round": round_num}})
    q.put(None)  # sentinel: tells the async side the thread is finished


@app.post("/chat")
async def chat(request: Request, x_session_token: str | None = Header(None)):
    user_id = _require_session(x_session_token)
    body = await request.json()
    node_id = body.get("node_id") or ""
    message = body.get("message") or ""
    if not node_id or not message:
        raise HTTPException(status_code=422, detail="node_id and message are required")

    round_num = await db.start_round(node_id, message, user_id)

    async def stream():
        loop = asyncio.get_event_loop()
        q: "queue.Queue[dict | None]" = queue.Queue()
        threading.Thread(
            target=_run_agent, args=(q, message, round_num), daemon=True
        ).start()

        events: List[Dict[str, Any]] = []
        text_parts: List[str] = []
        while True:
            event = await loop.run_in_executor(None, q.get)
            if event is None:
                break
            if event["type"] == "text_delta":
                text_parts.append(event["data"]["delta"])
            if event["type"] not in ("done",):
                events.append(event)
            yield f"data: {json.dumps(event)}\n\n"

        await db.finish_round(
            node_id,
            round_num,
            {
                "text": "".join(text_parts),
                "events": events,
                "result": {},
                "mode": "insecure_agent",
                "interrupted": False,
            },
            user_id,
        )

    return StreamingResponse(stream(), media_type="text/event-stream")

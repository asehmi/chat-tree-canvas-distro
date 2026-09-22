# Copyright (c) 2026 Arvindra Sehmi
# Licensed under the MIT License — see LICENSE for details.

"""OpenRouter client for the insecure agent provider.

Ported from a sibling project's `lib/llm/openrouter_client.py`, decoupled
from that project's settings/logging modules. Plain `httpx`, no SDK —
OpenRouter's API is OpenAI-schema-compatible, so this is the one provider
client any OpenAI-compatible endpoint could reuse with just a `base_url`
change. Synchronous throughout (`httpx.Client`, not `AsyncClient`),
matching `AnthropicClient`/`GeminiClient` and this provider's own
synchronous request-handling thread.

Tool calls only stream over OpenRouter's SSE format ("tools can only be
used with streaming responses" for this hand-rolled parsing approach), so
both `generate_response`'s tool path and `generate_response_stream` go
through the same `SSEParser`.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Iterator
from typing import Any

import httpx
from tenacity import Retrying, stop_after_attempt, wait_exponential

from .tools import EndTurn, ToolCall, ToolCallLoopGuard

logger = logging.getLogger("insecure_agent.llm")

DEFAULT_MODEL = "google/gemini-3.7-flash"
BASE_URL = "https://openrouter.ai/api/v1"


def _log_retry_attempt(retry_state) -> None:
    logger.debug(f"[LLM] attempt {retry_state.attempt_number} failed, retrying...")


class SSEParser:
    """Buffers and parses an OpenAI-style SSE stream, extracting content
    deltas and tool-call fragments. Synchronous — see module docstring.

    Tool-call fragments are keyed by the delta's own `index`, not `id`:
    only the *first* fragment of a tool call reliably carries `id`; later
    fragments carrying the rest of `arguments` may omit it and carry only
    `index`, which stays stable across every fragment of the same call.
    """

    def __init__(self) -> None:
        self.response_text = ""
        self._tool_call_buffer: dict[int, dict[str, str]] = {}
        self._json_buffer = ""

    def parse(self, response: httpx.Response) -> Iterator[str]:
        """Parse the SSE stream, yielding each content delta as it's
        decoded, while populating `response_text`/the tool-call buffer as
        a side effect. A caller that only wants the accumulated result has
        to fully drain the generator, e.g. `for _ in parser.parse(response):
        pass` — `parse()` being a generator means nothing inside it runs
        until iterated."""
        for chunk in response.iter_bytes(chunk_size=4096):
            self._json_buffer += chunk.decode("utf-8")
            while True:
                line_end = self._json_buffer.find("\n")
                if line_end == -1:
                    break
                line = self._json_buffer[:line_end].strip()
                self._json_buffer = self._json_buffer[line_end + 1:]
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    return
                try:
                    delta = json.loads(data)["choices"][0]["delta"]
                except json.JSONDecodeError:
                    self._json_buffer = f"data: {data}\n" + self._json_buffer
                    break
                if content := delta.get("content"):
                    self.response_text += content
                    yield content
                for tc in delta.get("tool_calls") or []:
                    position = tc.get("index", 0)
                    buf = self._tool_call_buffer.setdefault(
                        position, {"id": "", "name": "", "arguments_buffer": ""}
                    )
                    if tc.get("id"):
                        buf["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name") and not buf["name"]:
                        buf["name"] = fn["name"]
                    if fn.get("arguments"):
                        buf["arguments_buffer"] += fn["arguments"]

    def get_tool_calls(self) -> list[ToolCall]:
        calls = []
        for position, buf in self._tool_call_buffer.items():
            if not buf["name"] or not buf["arguments_buffer"].strip():
                continue
            try:
                args = json.loads(buf["arguments_buffer"])
            except json.JSONDecodeError:
                continue
            call_id = buf["id"] or f"tool_{position}"
            calls.append(ToolCall(id=call_id, name=buf["name"], arguments=args))
        return calls


def _to_openrouter_tools(definitions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": d["name"],
                "description": d["description"],
                "parameters": d["parameters"],
            },
        }
        for d in definitions
    ]


class OpenRouterClient:
    """Client for OpenRouter's OpenAI-compatible chat completions API."""

    def __init__(
        self, api_key: str | None = None, model: str = DEFAULT_MODEL, base_url: str = BASE_URL
    ):
        api_key = api_key or os.getenv("OPENROUTER_API_KEY") or os.getenv("OR_API_KEY")
        if not api_key:
            raise ValueError(
                "OpenRouter API key not configured. Set OPENROUTER_API_KEY (or "
                "OR_API_KEY) in .env."
            )
        self.api_key = api_key
        self.model_name = model
        self.base_url = base_url

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-Title": "chat-tree-canvas insecure agent provider",
        }

    def generate_response(
        self,
        system_instruction: str,
        prompt: str,
        response_schema: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_handler: Callable[[ToolCall], str] | None = None,
    ) -> str | dict[str, Any]:
        """Generate a response. See `AnthropicClient.generate_response` for
        the shared contract. Routes through the streaming/tool-call loop
        only when `tools` is given — a plain non-streaming call otherwise."""
        final_prompt = prompt
        if response_schema is not None:
            schema_str = json.dumps(response_schema, indent=2)
            final_prompt = (
                f"{prompt}\n\nRespond ONLY with a valid JSON object matching "
                f"this schema exactly. No markdown fences, no explanation:\n"
                f"{schema_str}"
            )

        retrying = Retrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=2, max=10),
            before_sleep=_log_retry_attempt,
            reraise=True,
        )
        for attempt in retrying:
            with attempt:
                if tools and tool_handler:
                    text_content = self._run_with_tools(
                        system_instruction, final_prompt, tools, tool_handler
                    )
                else:
                    text_content = self._generate_once(system_instruction, final_prompt)

                if not text_content:
                    raise ValueError("OpenRouter returned an empty response")

                if response_schema is None:
                    logger.info("[OPENROUTER] response received")
                    return text_content

                parsed = json.loads(text_content)
                logger.info("[OPENROUTER] response received and parsed as JSON")
                return parsed

        raise RuntimeError("unreachable: Retrying always returns or raises")  # pragma: no cover

    def _generate_once(self, system_instruction: str, prompt: str) -> str:
        logger.info(f"[OPENROUTER] calling {self.model_name}")
        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
        }
        with httpx.Client(timeout=httpx.Timeout(120)) as client:
            response = client.post(
                f"{self.base_url}/chat/completions", headers=self._headers(), json=payload
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]

    def _run_with_tools(
        self,
        system_instruction: str,
        prompt: str,
        tools: list[dict[str, Any]],
        tool_handler: Callable[[ToolCall], str],
    ) -> str:
        """The agentic loop: stream, parse tool calls, execute them, feed
        results back, repeat until the model stops asking for tools.

        Follow-up messages use the real OpenAI tool-calling protocol — an
        `assistant` turn carrying the `tool_calls` it made, then one `tool`
        turn per call keyed by `tool_call_id` — not a plain `user` message
        describing the result as text; with the latter, a model can fail
        to recognise its own tool call as answered and just call the same
        tool again, indefinitely. `ToolCallLoopGuard` is the backstop in
        case a model still won't converge even with the correct protocol.
        """
        openrouter_tools = _to_openrouter_tools(tools)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": prompt},
        ]
        loop_guard = ToolCallLoopGuard()

        while True:
            payload = {
                "model": self.model_name,
                "messages": messages,
                "tools": openrouter_tools,
                "stream": True,
            }
            with httpx.Client(timeout=httpx.Timeout(300)) as client:
                with client.stream(
                    "POST", f"{self.base_url}/chat/completions",
                    headers=self._headers(), json=payload,
                ) as response:
                    response.raise_for_status()
                    parser = SSEParser()
                    for _ in parser.parse(response):
                        pass

            tool_calls = parser.get_tool_calls()
            if not tool_calls:
                return parser.response_text
            loop_guard.check(tool_calls)

            messages.append({
                "role": "assistant",
                "content": parser.response_text or None,
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(call.arguments),
                        },
                    }
                    for call in tool_calls
                ],
            })
            for call in tool_calls:
                logger.info(f"[OPENROUTER] tool call: {call.name}({call.arguments})")
                result_text = tool_handler(call)
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": result_text,
                })

    def generate_response_stream(
        self,
        system_instruction: str,
        prompt: str,
        tools: list[dict[str, Any]] | None = None,
        tool_handler: Callable[[ToolCall], str] | None = None,
    ) -> Iterator[str]:
        """Like `generate_response`, but yields text as it arrives instead
        of waiting for the full response. See
        `AnthropicClient.generate_response_stream` for the shared contract
        (no `response_schema`, no retry wrapper). Same agentic loop as
        `_run_with_tools`, `yield from`ing `SSEParser`'s generator `parse()`
        instead of draining it, so a caller sees each round's text live."""
        openrouter_tools = _to_openrouter_tools(tools) if tools else None
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": prompt},
        ]
        loop_guard = ToolCallLoopGuard()

        while True:
            payload: dict[str, Any] = {
                "model": self.model_name,
                "messages": messages,
                "stream": True,
            }
            if openrouter_tools:
                payload["tools"] = openrouter_tools

            logger.info(f"[OPENROUTER] streaming call to {self.model_name}")
            with httpx.Client(timeout=httpx.Timeout(300)) as client:
                with client.stream(
                    "POST", f"{self.base_url}/chat/completions",
                    headers=self._headers(), json=payload,
                ) as response:
                    response.raise_for_status()
                    parser = SSEParser()
                    yield from parser.parse(response)

            tool_calls = parser.get_tool_calls()
            if not tool_calls or not tool_handler:
                return
            loop_guard.check(tool_calls)

            messages.append({
                "role": "assistant",
                "content": parser.response_text or None,
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(call.arguments),
                        },
                    }
                    for call in tool_calls
                ],
            })
            for call in tool_calls:
                logger.info(f"[OPENROUTER] tool call: {call.name}({call.arguments})")
                try:
                    result_text = tool_handler(call)
                except EndTurn:
                    return
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": result_text,
                })

# Copyright (c) 2026 Arvindra Sehmi
# Licensed under the MIT License — see LICENSE for details.

"""Gemini client for the insecure agent provider.

Ported from a sibling project's `lib/llm/gemini_client.py`, decoupled from
that project's settings/logging modules. Synchronous (`client.models.
generate_content`, not the `async` `client.aio.*` surface), matching
`AnthropicClient` and this provider's own synchronous request-handling
thread (see insecure_agent_provider.py's `_run_agent`).

The tool-calling loop (parsing `function_call` parts, building the
follow-up `function_response` turn) follows the standard `google-genai`
function-calling shape.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Iterator
from typing import Any

from tenacity import Retrying, stop_after_attempt, wait_exponential

from .tools import EndTurn, ToolCall, ToolCallLoopGuard

logger = logging.getLogger("insecure_agent.llm")

DEFAULT_MODEL = "gemini-3.1-flash-lite"


def _log_retry_attempt(retry_state) -> None:
    logger.debug(f"[LLM] attempt {retry_state.attempt_number} failed, retrying...")


def _to_gemini_tool(definitions: list[dict[str, Any]]):
    from google.genai import types

    return types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name=d["name"], description=d["description"], parameters=d["parameters"]
            )
            for d in definitions
        ]
    )


class GeminiClient:
    """Client for Google's Gemini API."""

    def __init__(self, api_key: str | None = None, model: str = DEFAULT_MODEL):
        from google import genai

        api_key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError(
                "Gemini API key not configured. Set GEMINI_API_KEY (or "
                "GOOGLE_API_KEY) in .env."
            )
        self.api_key = api_key
        self.model_name = model
        self._client = genai.Client(api_key=api_key)

    def generate_response(
        self,
        system_instruction: str,
        prompt: str,
        response_schema: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_handler: Callable[[ToolCall], str] | None = None,
    ) -> str | dict[str, Any]:
        """Generate a response. See `AnthropicClient.generate_response` for
        the shared contract (tools/tool_handler run an agentic loop; a
        given `response_schema` still applies to the final text)."""
        from google.genai import types

        config_kwargs: dict[str, Any] = {"system_instruction": system_instruction}
        if response_schema is not None:
            config_kwargs["response_mime_type"] = "application/json"
            config_kwargs["response_schema"] = response_schema
        if tools:
            config_kwargs["tools"] = [_to_gemini_tool(tools)]

        retrying = Retrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=2, max=10),
            before_sleep=_log_retry_attempt,
            reraise=True,
        )
        text = ""
        for attempt in retrying:
            with attempt:
                text = self._run(prompt, config_kwargs, tool_handler)

        if response_schema is None:
            logger.info("[GEMINI] response received")
            return text

        parsed = json.loads(text)
        logger.info("[GEMINI] response received and parsed as JSON")
        return parsed

    def _run(
        self,
        prompt: str,
        config_kwargs: dict[str, Any],
        tool_handler: Callable[[ToolCall], str] | None,
    ) -> str:
        """One full attempt: the agentic loop, ending when the model stops
        requesting tools. Retried as a whole by `generate_response`."""
        from google.genai import types

        contents: list[Any] = [prompt]
        loop_guard = ToolCallLoopGuard()
        response = None
        while True:
            logger.info(f"[GEMINI] calling {self.model_name}")
            response = self._client.models.generate_content(
                model=self.model_name,
                contents=contents,
                config=types.GenerateContentConfig(**config_kwargs),
            )

            function_calls = [
                part.function_call
                for part in response.candidates[0].content.parts
                if getattr(part, "function_call", None)
            ]
            if not function_calls or not tool_handler:
                break

            calls = [
                ToolCall(id=fc.name, name=fc.name, arguments=dict(fc.args or {}))
                for fc in function_calls
            ]
            loop_guard.check(calls)

            contents.append(response.candidates[0].content)
            response_parts = []
            for call in calls:
                logger.info(f"[GEMINI] tool call: {call.name}({call.arguments})")
                result_text = tool_handler(call)
                response_parts.append(
                    types.Part(
                        function_response=types.FunctionResponse(
                            name=call.name, response={"result": result_text}
                        )
                    )
                )
            contents.append(types.Content(role="user", parts=response_parts))

        if not response.text:
            raise ValueError("Gemini returned an empty response")
        return response.text

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
        (no `response_schema`, no retry wrapper — a mid-stream failure
        raises rather than silently retrying over content a caller may
        already be showing).

        Each streamed chunk's parts are collected into `accumulated_parts`
        as they're seen — text parts already yielded to the caller, plus
        any `function_call` parts — so that if the model does call a tool,
        the full turn (not just whichever chunk happened to carry the
        call) is what gets appended to `contents` for the follow-up round.
        """
        from google.genai import types

        config_kwargs: dict[str, Any] = {"system_instruction": system_instruction}
        if tools:
            config_kwargs["tools"] = [_to_gemini_tool(tools)]

        contents: list[Any] = [prompt]
        loop_guard = ToolCallLoopGuard()

        while True:
            logger.info(f"[GEMINI] streaming call to {self.model_name}")
            accumulated_parts: list[Any] = []
            function_calls: list[Any] = []
            for chunk in self._client.models.generate_content_stream(
                model=self.model_name,
                contents=contents,
                config=types.GenerateContentConfig(**config_kwargs),
            ):
                if not chunk.candidates or not chunk.candidates[0].content:
                    continue
                for part in chunk.candidates[0].content.parts or []:
                    if getattr(part, "function_call", None):
                        function_calls.append(part.function_call)
                        accumulated_parts.append(part)
                    elif getattr(part, "text", None):
                        yield part.text
                        accumulated_parts.append(part)

            if not function_calls or not tool_handler:
                return

            calls = [
                ToolCall(id=fc.name, name=fc.name, arguments=dict(fc.args or {}))
                for fc in function_calls
            ]
            loop_guard.check(calls)

            contents.append(types.Content(role="model", parts=accumulated_parts))
            response_parts = []
            for call in calls:
                logger.info(f"[GEMINI] tool call: {call.name}({call.arguments})")
                try:
                    result_text = tool_handler(call)
                except EndTurn:
                    return
                response_parts.append(
                    types.Part(
                        function_response=types.FunctionResponse(
                            name=call.name, response={"result": result_text}
                        )
                    )
                )
            contents.append(types.Content(role="user", parts=response_parts))

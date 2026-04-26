"""Stateless Claude agent.

Takes a pre-built messages list from the caller. The Discord layer is responsible
for hydrating context from channel history (option 3 — Discord is the source of
truth, the agent holds no conversation state).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from anthropic import AsyncAnthropic

from .mcp_client import McpClient

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 15
MAX_TOOL_RESULT_CHARS = 30_000


@dataclass
class AgentReply:
    text: str
    is_complete: bool
    iterations_used: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int


class HandlerAgent:
    """Wraps the Anthropic client + MCP tools. No conversation state."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        max_tokens: int,
        effort: str,
        system_prompt: str,
        mcp: McpClient,
    ) -> None:
        self._client = AsyncAnthropic(api_key=api_key)
        self._model = model
        self._max_tokens = max_tokens
        self._effort = effort
        self._system_prompt = system_prompt
        self._mcp = mcp

    async def reply(self, messages: list[dict[str, Any]]) -> AgentReply:
        """Run the agentic loop against the given messages and return the final text.

        ``messages`` must already follow the Anthropic alternation rule
        (first message role=user, then strict user/assistant alternation). The
        Discord layer is responsible for building this from channel history.
        """
        working = list(messages)
        tools = self._mcp.tools()
        total_input = total_output = total_cache_read = total_cache_create = 0
        iterations = 0
        response = None

        for iteration in range(MAX_TOOL_ITERATIONS):
            iterations = iteration + 1
            extra: dict[str, Any] = dict(self._opus_params())
            if tools:
                extra["tools"] = tools
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=[
                    {
                        "type": "text",
                        "text": self._system_prompt,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=working,
                **extra,
            )

            usage = getattr(response, "usage", None)
            if usage is not None:
                total_input += getattr(usage, "input_tokens", 0)
                total_output += getattr(usage, "output_tokens", 0)
                total_cache_read += getattr(usage, "cache_read_input_tokens", 0)
                total_cache_create += getattr(usage, "cache_creation_input_tokens", 0)

            if response.stop_reason == "end_turn":
                return AgentReply(
                    text=_extract_text(response.content),
                    is_complete=True,
                    iterations_used=iterations,
                    input_tokens=total_input,
                    output_tokens=total_output,
                    cache_read_tokens=total_cache_read,
                    cache_creation_tokens=total_cache_create,
                )

            if response.stop_reason == "tool_use":
                working.append({"role": "assistant", "content": response.content})
                tool_results = await self._run_tools(response.content)
                working.append({"role": "user", "content": tool_results})
                continue

            logger.warning("Unexpected stop_reason=%s, ending loop", response.stop_reason)
            break

        text = _extract_text(response.content) if response is not None else ""
        return AgentReply(
            text=text or "I ran out of tool budget before finishing. Try a more specific question.",
            is_complete=False,
            iterations_used=iterations,
            input_tokens=total_input,
            output_tokens=total_output,
            cache_read_tokens=total_cache_read,
            cache_creation_tokens=total_cache_create,
        )

    def _opus_params(self) -> dict[str, Any]:
        # Adaptive thinking + effort apply to Opus 4.6/4.7 and Sonnet 4.6.
        # Haiku 4.5 doesn't support effort (returns 400) and we don't pass thinking.
        if self._model.startswith(("claude-opus-4-7", "claude-opus-4-6", "claude-sonnet-4-6")):
            return {
                "thinking": {"type": "adaptive"},
                "output_config": {"effort": self._effort},
            }
        return {}

    async def _run_tools(self, content: list[Any]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for block in content:
            if getattr(block, "type", None) != "tool_use":
                continue
            try:
                output = await self._mcp.call_tool(block.name, block.input or {})
            except Exception as exc:
                logger.exception("Tool %s failed", block.name)
                output = f"Tool '{block.name}' failed: {exc}"
            if len(output) > MAX_TOOL_RESULT_CHARS:
                output = output[:MAX_TOOL_RESULT_CHARS] + "\n[truncated]"
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": output,
                }
            )
        return results


def _extract_text(content: list[Any]) -> str:
    parts: list[str] = []
    for block in content:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    return "\n".join(parts).strip()

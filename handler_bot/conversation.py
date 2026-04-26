"""Per-channel conversation history with TTL-based eviction."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

CONVERSATION_TTL_SECONDS = 7 * 24 * 3600
MAX_TRACKED_CONVERSATIONS = 500


@dataclass
class Conversation:
    messages: list[dict[str, Any]] = field(default_factory=list)
    last_active: float = field(default_factory=time.monotonic)


class ConversationStore:
    """Holds in-memory conversation histories keyed by Discord channel/thread ID."""

    def __init__(self, max_history: int) -> None:
        self._max_history = max_history
        self._convos: dict[str, Conversation] = {}

    def get(self, key: str) -> Conversation:
        convo = self._convos.get(key)
        if convo is None:
            convo = Conversation()
            self._convos[key] = convo
            self._evict_stale()
        convo.last_active = time.monotonic()
        return convo

    def reset(self, key: str) -> bool:
        return self._convos.pop(key, None) is not None

    def trim(self, key: str) -> None:
        """Drop oldest turns past the configured window, preserving tool pairings.

        Tool calls and their results must stay together — splitting them produces
        an API error on the next request. We walk forward from the trim point
        until we hit a plain user message before dropping.
        """
        convo = self._convos.get(key)
        if convo is None or len(convo.messages) <= self._max_history:
            return

        candidate = convo.messages[-self._max_history :]
        for i, msg in enumerate(candidate):
            if msg["role"] == "user" and not _is_tool_result(msg):
                convo.messages = candidate[i:]
                return
        convo.messages = candidate

    def _evict_stale(self) -> None:
        now = time.monotonic()
        stale = [k for k, c in self._convos.items() if now - c.last_active > CONVERSATION_TTL_SECONDS]
        for k in stale:
            self._convos.pop(k, None)
        if len(self._convos) > MAX_TRACKED_CONVERSATIONS:
            oldest = sorted(self._convos.items(), key=lambda kv: kv[1].last_active)
            for k, _ in oldest[: len(self._convos) - MAX_TRACKED_CONVERSATIONS]:
                self._convos.pop(k, None)


def _is_tool_result(msg: dict[str, Any]) -> bool:
    content = msg.get("content")
    if isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, dict) and first.get("type") == "tool_result":
            return True
    return False

"""Build Anthropic-format messages from a Discord channel's recent history.

Option 3: Discord is the source of truth. Every turn, fetch the last N messages
from the channel and convert them into the alternating user/assistant shape the
Anthropic API requires. No local state — restart-safe and always consistent
with what's actually visible in the channel.

Multi-speaker handling: human messages get a ``[Display Name]:`` prefix so
Claude can tell speakers apart in a group chat. Consecutive same-role messages
are merged into a single turn (the API rejects two user-turns or two
assistant-turns in a row).
"""

from __future__ import annotations

import logging
from typing import Any

import discord

logger = logging.getLogger(__name__)


async def fetch_messages(
    channel: discord.abc.Messageable,
    bot_user_id: int,
    limit: int,
) -> list[dict[str, Any]]:
    """Fetch the last ``limit`` messages and return them in Anthropic format.

    Returns a list of ``{"role": "user"|"assistant", "content": str}`` dicts,
    chronological order, ready to pass to ``HandlerAgent.reply``.
    """
    raw: list[discord.Message] = []
    async for msg in channel.history(limit=limit):
        raw.append(msg)
    raw.reverse()  # discord returns newest-first; we want oldest-first

    turns: list[tuple[str, str]] = []  # (role, formatted_content)
    for msg in raw:
        text = (msg.clean_content or "").strip()
        if not text:
            continue  # skip attachment-only / system messages

        if msg.author.id == bot_user_id:
            turns.append(("assistant", text))
        else:
            speaker = msg.author.display_name or msg.author.name
            turns.append(("user", f"[{speaker}]: {text}"))

    # Anthropic requires the first message to be role=user. Drop leading
    # assistant turns (rare — happens if the bot was the most recent speaker
    # before any human spoke in the visible window).
    while turns and turns[0][0] == "assistant":
        turns.pop(0)

    if not turns:
        return []

    # Merge consecutive same-role turns into one (newline-joined). Required
    # because the API rejects two user or two assistant messages in a row.
    merged: list[dict[str, Any]] = []
    cur_role, cur_lines = turns[0][0], [turns[0][1]]
    for role, content in turns[1:]:
        if role == cur_role:
            cur_lines.append(content)
        else:
            merged.append({"role": cur_role, "content": "\n".join(cur_lines)})
            cur_role, cur_lines = role, [content]
    merged.append({"role": cur_role, "content": "\n".join(cur_lines)})

    return merged

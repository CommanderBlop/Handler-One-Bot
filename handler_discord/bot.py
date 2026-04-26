"""Discord client — listens for mentions/DMs, hydrates context from channel
history, dispatches to the stateless Claude agent."""

from __future__ import annotations

import asyncio
import logging

import discord

from handler_bot.agent import HandlerAgent

from .chunker import chunk_for_discord
from .history import fetch_messages

logger = logging.getLogger(__name__)


class HandlerDiscordBot(discord.Client):
    """Subclass of ``discord.Client`` wired to the Claude agent."""

    def __init__(
        self,
        *,
        agent: HandlerAgent,
        history_limit: int,
        query_timeout: int,
        allowed_user_ids: set[int],
        allowed_guild_ids: set[int],
    ) -> None:
        intents = discord.Intents.default()
        intents.message_content = True  # required to read message text
        intents.dm_messages = True
        super().__init__(intents=intents)

        self._agent = agent
        self._history_limit = history_limit
        self._query_timeout = query_timeout
        self._allowed_users = allowed_user_ids
        self._allowed_guilds = allowed_guild_ids

    async def on_ready(self) -> None:
        logger.info("Logged in as %s (id=%s)", self.user, self.user.id if self.user else "?")
        logger.info(
            "Listening: %d guild(s), allowlist users=%s guilds=%s, history=%d msgs/turn",
            len(self.guilds),
            self._allowed_users or "any",
            self._allowed_guilds or "any",
            self._history_limit,
        )

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or self.user is None:
            return

        is_dm = isinstance(message.channel, discord.DMChannel)
        mentioned = self.user in message.mentions
        if not (is_dm or mentioned):
            return

        if not self._is_authorized(message):
            logger.info("Rejecting unauthorized message from %s", message.author.id)
            return

        async with message.channel.typing():
            try:
                messages = await fetch_messages(
                    message.channel, self.user.id, self._history_limit
                )
                if not messages:
                    await message.channel.send("(no message history to work with — try again)")
                    return

                reply = await asyncio.wait_for(
                    self._agent.reply(messages), timeout=self._query_timeout
                )
            except asyncio.TimeoutError:
                await message.channel.send(
                    f"Timed out after {self._query_timeout}s. Try a tighter question?"
                )
                return
            except Exception:
                logger.exception("Agent error")
                await message.channel.send("Something broke on my end. Try again in a sec.")
                return

        for chunk in chunk_for_discord(reply.text or "(no response)"):
            await message.channel.send(chunk)

        if not reply.is_complete:
            await message.channel.send(
                "_(Hit the tool-call limit before finishing. Ask again to keep going.)_"
            )

        logger.info(
            "answered chan=%s msgs=%d iters=%d in=%d out=%d cache_read=%d",
            message.channel.id,
            len(messages),
            reply.iterations_used,
            reply.input_tokens,
            reply.output_tokens,
            reply.cache_read_tokens,
        )

    def _is_authorized(self, message: discord.Message) -> bool:
        if self._allowed_users and message.author.id not in self._allowed_users:
            return False
        if self._allowed_guilds and message.guild and message.guild.id not in self._allowed_guilds:
            return False
        return True

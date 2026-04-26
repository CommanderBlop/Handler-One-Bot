"""Discord client — listens for mentions/DMs and routes to the Claude agent."""

from __future__ import annotations

import asyncio
import logging

import discord

from handler_bot.agent import HandlerAgent

from .chunker import chunk_for_discord

logger = logging.getLogger(__name__)


class HandlerDiscordBot(discord.Client):
    """Subclass of ``discord.Client`` wired to the Claude agent."""

    def __init__(
        self,
        *,
        agent: HandlerAgent,
        query_timeout: int,
        allowed_user_ids: set[int],
        allowed_guild_ids: set[int],
    ) -> None:
        intents = discord.Intents.default()
        intents.message_content = True  # required to read message text
        intents.dm_messages = True
        super().__init__(intents=intents)

        self._agent = agent
        self._query_timeout = query_timeout
        self._allowed_users = allowed_user_ids
        self._allowed_guilds = allowed_guild_ids

    async def on_ready(self) -> None:
        logger.info("Logged in as %s (id=%s)", self.user, self.user.id if self.user else "?")
        logger.info(
            "Listening: %d guild(s), allowlist users=%s guilds=%s",
            len(self.guilds),
            self._allowed_users or "any",
            self._allowed_guilds or "any",
        )

    async def on_message(self, message: discord.Message) -> None:
        logger.debug(
            "on_message: author=%s bot=%s channel=%s mentions=%s content=%r",
            message.author, message.author.bot, message.channel.id,
            [u.id for u in message.mentions], message.content[:80],
        )
        if message.author.bot or self.user is None:
            return

        is_dm = isinstance(message.channel, discord.DMChannel)
        mentioned = self.user in message.mentions
        if not (is_dm or mentioned):
            return

        if not self._is_authorized(message):
            logger.info("Rejecting unauthorized message from %s", message.author.id)
            return

        prompt = self._strip_mention(message.content)
        if not prompt:
            await message.channel.send(f"Hey {message.author.mention} — ask me something.")
            return

        if prompt.lower().strip() in {"!reset", "!clear", "/reset"}:
            self._agent.reset(self._conversation_key(message))
            await message.channel.send("Conversation reset.")
            return

        async with message.channel.typing():
            try:
                reply = await asyncio.wait_for(
                    self._agent.ask(self._conversation_key(message), prompt),
                    timeout=self._query_timeout,
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
                "_(Hit the tool-call limit before I was done. Reply for more, or `!reset`.)_"
            )

        logger.info(
            "answered chat=%s iters=%d in=%d out=%d cache_read=%d",
            self._conversation_key(message),
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

    def _strip_mention(self, content: str) -> str:
        if self.user is None:
            return content.strip()
        for tag in (f"<@{self.user.id}>", f"<@!{self.user.id}>"):
            content = content.replace(tag, "")
        return content.strip()

    @staticmethod
    def _conversation_key(message: discord.Message) -> str:
        # Threads, DMs, and guild channels each get their own bucket.
        return f"chan:{message.channel.id}"

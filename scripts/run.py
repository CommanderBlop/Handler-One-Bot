#!/usr/bin/env python3
"""Entry point: load config, wire up the agent + Discord bot, run forever."""

from __future__ import annotations

import asyncio
import logging
import signal

from dotenv import load_dotenv

from handler_bot.agent import HandlerAgent
from handler_bot.config import Settings
from handler_bot.conversation import ConversationStore
from handler_bot.mcp_client import McpClient
from handler_bot.prompt import SYSTEM_PROMPT
from handler_discord.bot import HandlerDiscordBot


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # Discord.py is verbose; keep gateway/client at INFO (catches reconnects),
    # silence HTTP request spam unless we're explicitly debugging.
    logging.getLogger("discord.gateway").setLevel(logging.INFO)
    logging.getLogger("discord.client").setLevel(logging.INFO)
    logging.getLogger("discord.http").setLevel(logging.WARNING)


async def _amain() -> None:
    load_dotenv(override=False)
    settings = Settings()
    _setup_logging(settings.log_level)
    log = logging.getLogger("handler.run")

    mcp = McpClient()
    # When the restaurant MCP server lands, register it here:
    # await mcp.connect(McpServerSpec(name="restaurant", command="python",
    #                                 args=["-m", "restaurant_mcp.server"]))

    conversations = ConversationStore(max_history=settings.handler_max_history)
    agent = HandlerAgent(
        api_key=settings.anthropic_api_key,
        model=settings.claude_model,
        max_tokens=settings.claude_max_tokens,
        effort=settings.claude_effort,
        system_prompt=SYSTEM_PROMPT,
        conversations=conversations,
        mcp=mcp,
    )

    bot = HandlerDiscordBot(
        agent=agent,
        query_timeout=settings.handler_query_timeout,
        allowed_user_ids=settings.allowed_user_id_set,
        allowed_guild_ids=settings.allowed_guild_id_set,
    )

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            # Windows; fall back to default KeyboardInterrupt handling
            pass

    log.info("Starting Handler bot (model=%s)", settings.claude_model)
    bot_task = asyncio.create_task(bot.start(settings.discord_bot_token))

    try:
        done, _ = await asyncio.wait(
            {bot_task, asyncio.create_task(stop_event.wait())},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in done:
            if task is bot_task:
                task.result()  # surface bot errors
    finally:
        log.info("Shutting down...")
        await bot.close()
        await mcp.close()
        if not bot_task.done():
            bot_task.cancel()
            try:
                await bot_task
            except (asyncio.CancelledError, Exception):
                pass


def main() -> None:
    try:
        asyncio.run(_amain())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

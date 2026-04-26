#!/usr/bin/env python3
"""Entry point: load config, wire up the agent + Discord bot, run forever."""

from __future__ import annotations

import asyncio
import logging
import signal

from dotenv import load_dotenv

from handler_bot.agent import HandlerAgent
from handler_bot.config import Settings
from handler_bot.mcp_client import McpClient, McpServerSpec
from handler_bot.prompt import SYSTEM_PROMPT
from handler_discord.bot import HandlerDiscordBot


def _derive_butler_pythonpath(command: str) -> str:
    """Best-effort: …/<repo>/.venv/bin/python → …/<repo>."""
    from pathlib import Path
    p = Path(command).resolve()
    # If the command is .venv/bin/python, walk up two for .venv, one more for repo.
    if p.parent.name == "bin" and p.parent.parent.name == ".venv":
        return str(p.parent.parent.parent)
    return str(p.parent)


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
    if settings.butler_mcp_command:
        import os
        butler_env = {
            "BUTLER_API_BASE_URL": settings.butler_api_base_url,
            "PATH": os.environ.get("PATH", ""),
            # The butler MCP server is `python -m src.mcp_server` — the
            # subprocess needs PYTHONPATH pointing at the butler repo so
            # the import resolves. Pass through if the user set it; else
            # derive from the command path (assumes …/<repo>/.venv/bin/python).
            "PYTHONPATH": os.environ.get(
                "BUTLER_MCP_PYTHONPATH",
                _derive_butler_pythonpath(settings.butler_mcp_command),
            ),
        }
        if settings.butler_api_token:
            butler_env["BUTLER_API_TOKEN"] = settings.butler_api_token
        await mcp.connect(McpServerSpec(
            name="butler",
            command=settings.butler_mcp_command,
            args=[a for a in settings.butler_mcp_args.split() if a],
            env=butler_env,
        ))

    agent = HandlerAgent(
        api_key=settings.anthropic_api_key,
        model=settings.claude_model,
        max_tokens=settings.claude_max_tokens,
        effort=settings.claude_effort,
        system_prompt=SYSTEM_PROMPT,
        mcp=mcp,
    )

    bot = HandlerDiscordBot(
        agent=agent,
        history_limit=settings.handler_history_fetch_limit,
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

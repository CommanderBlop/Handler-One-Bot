# Handler-One Discord Bot

Personal AI assistant for Jack's Discord server. Powered by Claude (Anthropic
API) and fronted as a Discord bot. Designed to be portable, containerized, and
extensible — a future restaurant-reservation MCP server will plug in here.

## Architecture

```
Discord  <->  handler_discord  <->  handler_bot  <->  Anthropic API (Claude)
                                          |
                                          +---->  MCP servers (future: restaurant)
```

- **`handler_bot/`** — Stateless Claude agent layer. `agent.py` runs the async
  messages loop with prompt caching; `mcp_client.py` is the multi-server MCP
  foundation. No conversation state lives here.
- **`handler_discord/`** — Discord layer. `bot.py` listens for mentions/DMs;
  `history.py` fetches recent channel messages and converts them to the
  Anthropic format (multi-speaker `[Name]:` prefix); `chunker.py` splits long
  replies to fit the 2000-char message cap.
- **`scripts/run.py`** — Entry point. Wires everything together.

The bot is stateless: every turn it fetches the last N messages from the
Discord channel and passes them to Claude. Discord is the source of truth, so
the bot is restart-safe and can't drift out of sync. The system prompt is
prompt-cached (top-level `cache_control`) so per-turn cost stays low.

## Setup

### Prerequisites

- Python 3.11+
- An Anthropic API key — <https://console.anthropic.com/>
- A Discord application + bot token

### Create the Discord application

1. Go to <https://discord.com/developers/applications> and create a new app.
2. In **Bot**, click **Reset Token** to get the bot token (this goes in `.env`
   as `DISCORD_BOT_TOKEN`).
3. Enable **Message Content Intent** under Privileged Gateway Intents — the bot
   needs this to read message text.
4. In **OAuth2 → URL Generator**, select scopes `bot` and `applications.commands`.
   For bot permissions pick: `View Channels`, `Send Messages`, `Read Message
   History`. (Add `Send Messages in Threads` if you want thread support.)
5. Open the generated URL to invite the bot to your server.

### Install + run locally

```bash
make venv                      # creates .venv and installs the package
cp .env.template .env          # then edit .env and fill in the secrets
make run                       # starts the bot
```

Mention the bot in a channel (`@Handler what's up?`) or DM it directly.

### Run in Docker

```bash
cp .env.template .env          # fill in secrets
make docker-up                 # build image + start container in background
make docker-logs               # tail logs
make docker-down               # stop
```

The container runs as a non-root user; no ports are exposed (the bot dials out
to Discord over WebSocket).

## Configuration

All config lives in `.env`. See `.env.template` for the full list. The most
relevant knobs:

| Variable             | Default              | Notes                                            |
|----------------------|----------------------|--------------------------------------------------|
| `ANTHROPIC_API_KEY`  | —                    | Required                                         |
| `DISCORD_BOT_TOKEN`  | —                    | Required                                         |
| `CLAUDE_MODEL`       | `claude-haiku-4-5`   | Or `claude-sonnet-4-6` / `claude-opus-4-7`       |
| `CLAUDE_MAX_TOKENS`  | `8000`               | Per-response output cap                          |
| `CLAUDE_EFFORT`      | `high`               | `low` / `medium` / `high` / `xhigh` / `max`      |
| `HANDLER_HISTORY_FETCH_LIMIT` | `30`        | Discord messages fetched as context per turn     |
| `ALLOWED_USER_IDS`   | (any)                | Comma-separated; empty = anyone                  |
| `ALLOWED_GUILD_IDS`  | (any)                | Comma-separated; empty = any server              |

## Restaurant reservations (Butler integration)

Handler can drive [restaurant-butler](https://github.com/jackieinclair/restaurant-butler)
— a separate daemon that polls OpenTable and auto-books reservations when a
slot opens. When wired in, Claude gets `butler__*` tools for searching
OpenTable, managing the local restaurant catalog, creating reservation hunts
("quests"), and listing past/current bookings.

To enable: set the `BUTLER_*` vars in `.env`. The most important is
`BUTLER_MCP_COMMAND`, which should point at the butler repo's venv python so
the MCP subprocess has butler's dependencies. Example:

```
BUTLER_MCP_COMMAND=/Users/zakiaserver/Documents/restaurant-butler/.venv/bin/python
BUTLER_MCP_ARGS=-m src.mcp_server
BUTLER_API_BASE_URL=http://zakia-server.local:8765
```

Leave `BUTLER_MCP_COMMAND` blank to disable; the bot falls back to plain
chat-only mode.

**Docker caveat:** the butler subprocess is launched on the host filesystem
path you give it. The container can't see that path, so running handler-bot
inside docker with butler enabled won't work. Either run handler-bot natively
on the same host as butler (current setup), or expose butler's MCP server over
a network transport later (TODO).

## Roadmap

- [x] Bot layer + Discord layer + Claude integration
- [x] Restaurant reservation MCP server connection (butler)
- [ ] Web search + web fetch (Anthropic server-side tools)
- [ ] Slash commands

## Project layout

```
handler-one-discord-bot/
├── handler_bot/              # Stateless Claude agent layer
│   ├── agent.py              # Async messages loop, prompt caching, tool dispatch
│   ├── mcp_client.py         # Multi-server MCP foundation
│   ├── prompt.py             # System prompt
│   └── config.py             # pydantic-settings env loader
├── handler_discord/          # Discord layer
│   ├── bot.py                # discord.py client, mention/DM handling
│   ├── history.py            # Fetches channel history, formats for Anthropic
│   └── chunker.py            # 2000-char chunking with code-fence repair
├── scripts/run.py            # Entry point
├── docs/architecture.md      # Design notes
├── Dockerfile                # Multi-stage build, non-root runtime
├── docker-compose.yml        # Local dev orchestration
├── GNUmakefile               # make venv / run / docker-up / docker-logs
├── .env.template             # Copy to .env and fill in
└── pyproject.toml            # Dependencies + tool config
```

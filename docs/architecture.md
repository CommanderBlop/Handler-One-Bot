# Architecture

## Two layers, one MCP plug — Discord is the source of truth

```
+--------------------------------------------------+
|              handler_discord (frontend)          |
|  - discord.py Client + Intents                   |
|  - on_message: mentions, DMs                     |
|  - history.fetch_messages: pull last N messages, |
|    format with [Name]: prefixes, merge runs      |
|  - typing indicator + 2000-char chunking         |
+----------------------+---------------------------+
                       |  agent.reply(messages)
                       v
+--------------------------------------------------+
|             handler_bot (brain)                  |
|  - HandlerAgent: AsyncAnthropic + tool loop      |
|  - Stateless — no conversation store             |
|  - McpClient: multi-server MCP foundation        |
|  - prompt.py: system prompt (cached)             |
+----------------------+---------------------------+
                       |  messages.create()
                       v
              +---------------------+
              |  Anthropic API      |
              |  Claude Haiku 4.5   |
              +---------------------+

  (Future) handler_bot.mcp_client connects to:
    - restaurant-reservation MCP server (separate project)
    - any other MCP server we want
```

## Why this split

The Discord layer should know nothing about Claude, and the Claude layer should
know nothing about Discord. That makes it trivial to:

- Test the agent in isolation (no Discord token needed).
- Swap the frontend (a CLI, a web UI, Slack) without touching the agent.
- Reuse the same agent for multiple frontends in the future.

## Statelessness — option 3

Every time the bot is mentioned, `history.fetch_messages` pulls the last
`HANDLER_HISTORY_FETCH_LIMIT` messages (default 30) from the Discord channel
via `channel.history()` and converts them to the Anthropic format:

- Bot's own messages → `role: assistant`
- Anyone else's messages → `role: user`, content prefixed with `[Display Name]:`
- Consecutive same-role messages are merged into one turn (the API rejects two
  user-turns or two assistant-turns in a row)
- Leading assistant messages are dropped (the API requires the first message
  to be user)

The agent gets a complete, fresh messages list every call. No in-memory
conversation store. No persistence layer. No drift.

**Why this works well:**

- **Restart-safe**: bot crashes mid-conversation? Restart, mention it again,
  it picks up exactly where it left off because Discord remembers everything.
- **Multi-speaker by default**: the bot sees everything in the channel, not
  just messages addressed to it. Asking "what was Bob's point?" works.
- **No hidden state**: what you see in the channel is what the bot sees. No
  bugs from the bot remembering something that was edited or deleted.
- **Memory-cheap**: no per-channel buckets to evict. Server-friendly.

**The tradeoff**: one extra Discord REST call per mention (~50-100ms). For a
personal-use bot the latency is invisible; for a high-traffic public bot you'd
want a different design.

## Prompt caching

The system prompt is wrapped in a system block with `cache_control:
{type: "ephemeral"}`. Caches the system prompt (and any tool definitions)
across turns within the 5-minute TTL. Verify hits via
`usage.cache_read_input_tokens` in the logs. The system prompt must stay
byte-stable — don't interpolate timestamps or per-user data into it.

The conversation history itself is NOT cached. Channel history changes every
turn (new messages arrive), so caching it would just write-then-discard.

## Tool loop

When the MCP client has tools, every `messages.create()` may return
`stop_reason="tool_use"`. The loop:

1. Append the assistant message (including tool_use blocks) to the working
   messages list.
2. Execute each tool_use block via the MCP client.
3. Append a user message containing all tool_result blocks.
4. Loop. Cap at 15 iterations.

Tool names are namespaced as `<server>__<tool>` so multiple MCP servers can
expose tools with the same local name without collisions.

## Adding the restaurant MCP server

Two lines in `scripts/run.py`:

```python
from handler_bot.mcp_client import McpServerSpec
await mcp.connect(McpServerSpec(
    name="restaurant",
    command="python",
    args=["-m", "restaurant_mcp.server"],
))
```

Or, if it ships as a separate container, swap the stdio transport for SSE in
`mcp_client.py` (the MCP Python SDK supports both).

## Model choice

Default is `claude-haiku-4-5` ($1/M input, $5/M output, 64K max output, 200K
context). Cheap, fast, and plenty smart for a personal chat bot. Bump to
`claude-sonnet-4-6` for harder questions or `claude-opus-4-7` for the most
capable.

`HandlerAgent._opus_params()` gates the `thinking` and `output_config.effort`
parameters by model prefix — they're added for Opus 4.6/4.7 and Sonnet 4.6 but
skipped for Haiku 4.5 (which would 400 on `effort`). Switching `CLAUDE_MODEL`
in `.env` is enough; no code change.

## Containerization

`Dockerfile` is a two-stage build:

1. **builder** stage: installs the package into `/opt/venv`.
2. **runtime** stage: copies `/opt/venv` + source, switches to a non-root user,
   runs `python -m scripts.run`.

The image is `python:3.12-slim`-based and ends up around ~250MB. No ports are
exposed because the bot dials out to Discord; no host volumes are needed
because the bot is stateless.

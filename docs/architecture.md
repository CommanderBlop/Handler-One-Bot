# Architecture

## Two layers, one MCP plug

```
+--------------------------------------------------+
|              handler_discord (frontend)          |
|  - discord.py Client + Intents                   |
|  - on_message: mentions, DMs, !reset             |
|  - typing indicator + 2000-char chunking         |
+----------------------+---------------------------+
                       |  agent.ask(channel_id, text)
                       v
+--------------------------------------------------+
|             handler_bot (brain)                  |
|  - HandlerAgent: AsyncAnthropic + tool loop      |
|  - ConversationStore: per-channel history (TTL)  |
|  - McpClient: multi-server MCP foundation        |
|  - prompt.py: system prompt (cached)             |
+----------------------+---------------------------+
                       |  messages.create()
                       v
              +---------------------+
              |  Anthropic API      |
              |  Claude Opus 4.7    |
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

## Conversation state

History is keyed by Discord channel/thread ID. Each channel is its own
conversation — DMs, threads, and guild channels each get their own bucket.
History is in-memory only (good enough for personal use); restart clears it.
TTL eviction caps memory at ~500 conversations × 20 turns.

`!reset` drops the bucket on demand.

## Prompt caching

The system prompt is wrapped in a system block with `cache_control:
{type: "ephemeral"}`. This caches the system prompt + tool definitions across
turns within the 5-minute TTL window. Verify hits via `usage.cache_read_input_tokens`
in the logs. The system prompt must stay byte-stable — don't interpolate
timestamps or per-user data into it, or the cache invalidates.

## Tool loop

When the MCP client has tools, every `messages.create()` may return
`stop_reason="tool_use"`. The loop:

1. Append the assistant message (including tool_use blocks) to history.
2. Execute each tool_use block via the MCP client.
3. Append a user message containing all tool_result blocks.
4. Loop. Cap at 15 iterations.

Tool names are namespaced as `<server>__<tool>` so multiple MCP servers can
expose tools with the same local name without collisions.

## Adding the restaurant MCP server

When the restaurant MCP server is built, two lines in `scripts/run.py`:

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
because conversation state is intentionally ephemeral.

## What's intentionally NOT here

- **Persistent conversation storage.** In-memory is the right call for a
  friend-group bot. Restart = fresh slate, which is also useful when the prompt
  changes.
- **Slash commands.** Mention + DM handling is enough to start. Slash commands
  are easy to add later via `discord.app_commands` once the use case is clear.
- **Streaming.** Discord doesn't support live edits well, and for chat-length
  responses the latency is fine. We can add streaming later if a use case
  appears.
- **Cost tracking dashboard.** Logged per-turn for now; a real dashboard isn't
  worth building unless friends start hammering the bot.

"""MCP client foundation.

Currently inert — no servers configured. The agent calls `tools()` and gets back
an empty list, so Claude responds from its own knowledge. When the restaurant
reservation MCP server is ready (or any other), add a connection here and the
agent will pick up the new tools automatically.

Designed to support multiple MCP servers concurrently. Each server's tools are
namespaced by server name to avoid collisions (e.g. ``restaurant__make_booking``).
"""

from __future__ import annotations

import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

logger = logging.getLogger(__name__)

TOOL_NAMESPACE_SEP = "__"


@dataclass
class McpServerSpec:
    """How to launch / connect to an MCP server."""

    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] | None = None


@dataclass
class _ConnectedServer:
    name: str
    session: ClientSession
    # Real Anthropic-shaped tool definitions, captured at connect time
    # from the server's list_tools() response. Each entry is already
    # namespaced (`{server}__{name}`) for direct passthrough to Claude.
    tools: list[dict[str, Any]]


class McpClient:
    """Holds zero-or-more MCP server connections and exposes their tools."""

    def __init__(self) -> None:
        self._exit_stack = AsyncExitStack()
        self._servers: dict[str, _ConnectedServer] = {}
        self._tools_cache: list[dict[str, Any]] | None = None

    async def connect(self, spec: McpServerSpec) -> None:
        if spec.name in self._servers:
            raise ValueError(f"MCP server '{spec.name}' already connected")

        params = StdioServerParameters(command=spec.command, args=spec.args, env=spec.env)
        transport = await self._exit_stack.enter_async_context(stdio_client(params))
        read, write = transport
        session = await self._exit_stack.enter_async_context(ClientSession(read, write))
        await session.initialize()

        listing = await session.list_tools()
        tools = [self._to_anthropic_tool(spec.name, t) for t in listing.tools]
        self._servers[spec.name] = _ConnectedServer(spec.name, session, tools)
        self._tools_cache = None
        logger.info(
            "MCP server '%s' connected with tools: %s",
            spec.name, sorted(t["name"] for t in tools),
        )

    def tools(self) -> list[dict[str, Any]]:
        """Tool definitions in Anthropic API format, namespaced by server."""
        if self._tools_cache is not None:
            return self._tools_cache

        tools: list[dict[str, Any]] = []
        for server in self._servers.values():
            tools.extend(server.tools)
        self._tools_cache = tools
        return tools

    @staticmethod
    def _to_anthropic_tool(server: str, mcp_tool: Any) -> dict[str, Any]:
        """Convert an mcp.types.Tool into the Anthropic tool schema.

        MCP tools carry an `inputSchema` (JSON Schema). Anthropic's API
        wants the same JSON Schema under the key `input_schema`. The tool
        name is namespaced as `{server}__{name}` so call_tool can route.
        """
        schema = getattr(mcp_tool, "inputSchema", None) or {
            "type": "object", "properties": {},
        }
        # Anthropic requires `properties` (even if empty) and `type: object`.
        schema = dict(schema)
        schema.setdefault("type", "object")
        schema.setdefault("properties", {})
        return {
            "name": f"{server}{TOOL_NAMESPACE_SEP}{mcp_tool.name}",
            "description": (mcp_tool.description or "").strip()
                or f"Tool '{mcp_tool.name}' from MCP server '{server}'.",
            "input_schema": schema,
        }

    async def call_tool(self, namespaced_name: str, arguments: dict[str, Any]) -> str:
        if TOOL_NAMESPACE_SEP not in namespaced_name:
            raise ValueError(f"Tool name '{namespaced_name}' missing server namespace")
        server_name, tool_name = namespaced_name.split(TOOL_NAMESPACE_SEP, 1)
        server = self._servers.get(server_name)
        if server is None:
            raise ValueError(f"Unknown MCP server '{server_name}'")

        result = await server.session.call_tool(tool_name, arguments)
        parts: list[str] = []
        for item in result.content:
            text = getattr(item, "text", None)
            parts.append(text if text is not None else str(item))
        return "\n".join(parts)

    @property
    def has_tools(self) -> bool:
        return any(s.tools for s in self._servers.values())

    async def close(self) -> None:
        await self._exit_stack.aclose()

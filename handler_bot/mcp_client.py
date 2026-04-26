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
    tool_names: set[str]


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
        tool_names = {t.name for t in listing.tools}
        self._servers[spec.name] = _ConnectedServer(spec.name, session, tool_names)
        self._tools_cache = None
        logger.info("MCP server '%s' connected with tools: %s", spec.name, sorted(tool_names))

    def tools(self) -> list[dict[str, Any]]:
        """Tool definitions in Anthropic API format, namespaced by server."""
        if self._tools_cache is not None:
            return self._tools_cache

        tools: list[dict[str, Any]] = []
        for server in self._servers.values():
            # Re-listing here would be async; we cache the names at connect time
            # but the actual schema fetch happens on the agent's first call.
            for tool_name in sorted(server.tool_names):
                tools.append(self._placeholder_tool(server.name, tool_name))
        self._tools_cache = tools
        return tools

    @staticmethod
    def _placeholder_tool(server: str, name: str) -> dict[str, Any]:
        # When a real server is wired in, replace this with the schema returned
        # by `session.list_tools()`. Keeping it minimal here so the structure is
        # obvious and the agent code path is exercised end-to-end.
        return {
            "name": f"{server}{TOOL_NAMESPACE_SEP}{name}",
            "description": f"Tool '{name}' from MCP server '{server}'.",
            "input_schema": {"type": "object", "properties": {}},
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
        return any(s.tool_names for s in self._servers.values())

    async def close(self) -> None:
        await self._exit_stack.aclose()

"""Long-lived MCP client.

Owns the stdio subprocess + ClientSession for the lifetime of the
FastAPI app. The orchestrator passes the session to Gemini's
`tools=[session]` parameter; the SDK does tool discovery + dispatch.

We use the same MCPClient instance across all `/diagnose` requests —
the MCP Python SDK's session is safe to call concurrently (the stdio
transport serializes JSON-RPC frames under an internal lock).
"""

from __future__ import annotations

import logging
import os
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Optional

from mcp import StdioServerParameters
from mcp.client.session import ClientSession
from mcp.client.stdio import stdio_client

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MODULE = "backend.mcp_server.server"


class MCPClient:
    """Lifecycle wrapper around a persistent MCP stdio session."""

    def __init__(self) -> None:
        self._exit_stack: Optional[AsyncExitStack] = None
        self._session: Optional[ClientSession] = None
        self._tool_names: list[str] = []

    async def start(self) -> None:
        if self._session is not None:
            logger.info("MCP client already started.")
            return

        env = os.environ.copy()
        existing_pp = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            f"{_REPO_ROOT}{os.pathsep}{existing_pp}" if existing_pp else str(_REPO_ROOT)
        )
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", _MODULE],
            env=env,
        )

        self._exit_stack = AsyncExitStack()
        await self._exit_stack.__aenter__()

        read, write = await self._exit_stack.enter_async_context(stdio_client(params))
        session = await self._exit_stack.enter_async_context(ClientSession(read, write))
        await session.initialize()

        self._session = session
        try:
            tools = await session.list_tools()
            self._tool_names = [t.name for t in tools.tools]
            logger.info(
                "MCP client connected — %d tools available: %s",
                len(self._tool_names),
                ", ".join(self._tool_names),
            )
        except Exception as e:
            logger.warning("Could not enumerate MCP tools at startup: %s", e)
            self._tool_names = []

    async def stop(self) -> None:
        if self._exit_stack is None:
            return
        logger.info("Shutting down MCP client…")
        try:
            await self._exit_stack.__aexit__(None, None, None)
        except Exception as e:
            logger.warning("MCP shutdown raised: %s", e)
        finally:
            self._exit_stack = None
            self._session = None
            self._tool_names = []

    @property
    def session(self) -> Optional[ClientSession]:
        return self._session

    @property
    def is_alive(self) -> bool:
        return self._session is not None

    @property
    def tool_names(self) -> list[str]:
        return list(self._tool_names)


mcp_client = MCPClient()

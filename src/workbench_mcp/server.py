"""Entry point and server factory for the workbench MCP system."""
from __future__ import annotations

import logging

from mcp.server.fastmcp import FastMCP

from workbench_mcp.tools.database import register_database_tools
from workbench_mcp.tools.http_requests import register_http_tools
from workbench_mcp.tools.os_scripts import register_os_tools


LOGGER = logging.getLogger(__name__)


def build_server() -> FastMCP:
    """Create and configure the MCP server with all registered tools."""
    server = FastMCP("workbench-mcp")
    register_database_tools(server)
    register_http_tools(server)
    register_os_tools(server)
    return server


def main() -> None:
    """Initialize logging and launch the MCP server over standard I/O."""
    logging.basicConfig(level=logging.INFO)
    LOGGER.info("Initializing workbench-mcp server")
    build_server().run(transport="stdio")


if __name__ == "__main__":
    main()

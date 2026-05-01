from mcp.server.fastmcp import FastMCP
from app.config import settings
from app.tools import register_tools

# Create server with a descriptive name
mcp = FastMCP("DEVONthink MCP")

# Register all tools
register_tools(mcp)

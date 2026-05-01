import os
import argparse
import logging
from app.server import mcp

def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

def main():
    parser = argparse.ArgumentParser(description="Run the MCP server.")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "web"],
        help="Transport mode. If omitted, MCP_TRANSPORT is used (default: stdio).",
    )
    args = parser.parse_args()

    setup_logging()
    logging.info("Starting MCP server...")
    transport_mode = args.transport or os.environ.get("MCP_TRANSPORT", "stdio")
    if transport_mode in ("web", "sse"):
        mcp.run(transport="sse")
    else:
        mcp.run(transport="stdio")

if __name__ == "__main__":
    main()

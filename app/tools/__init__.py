"""Tool registration for DEVONthink MCP."""

import os

from app.tools.devonthink_tools import register_devonthink_tools
from app.tools.devonthink_annotation_tools import register_devonthink_annotation_tools
from app.tools.devonthink_database_tools import register_devonthink_database_tools
from app.tools.devonthink_dictionary_tools import register_devonthink_dictionary_tools
from app.tools.devonthink_link_tools import register_devonthink_link_tools
from app.tools.devonthink_reminder_tools import register_devonthink_reminder_tools
from app.tools.devonthink_richtext_tools import register_devonthink_richtext_tools
from app.tools.devonthink_script_tools import register_devonthink_script_tools
from app.tools.devonthink_smart_tools import register_devonthink_smart_tools


def register_tools(mcp):
    """Register all MCP tools.

    Profiles:
    - minimal: specialized tools only
    - canonical: specialized + canonical dictionary tools
    - full: specialized + all dictionary tools
    """
    profile = os.environ.get("DEVONTHINK_TOOL_PROFILE", "canonical").strip().lower()
    if profile not in {"minimal", "canonical", "full"}:
        profile = "canonical"

    register_devonthink_tools(mcp)
    register_devonthink_annotation_tools(mcp)
    register_devonthink_database_tools(mcp)
    register_devonthink_smart_tools(mcp)
    register_devonthink_reminder_tools(mcp)
    register_devonthink_script_tools(mcp)
    register_devonthink_richtext_tools(mcp)
    if profile == "minimal":
        return
    if profile == "canonical":
        register_devonthink_link_tools(mcp, include_tiers={"canonical"})
        register_devonthink_dictionary_tools(mcp, include_tiers={"canonical"})
        return
    register_devonthink_link_tools(mcp, include_tiers={"canonical", "advanced"})
    register_devonthink_dictionary_tools(mcp)

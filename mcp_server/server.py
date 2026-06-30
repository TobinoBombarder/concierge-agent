"""MCP server — exposes the concierge's read-only tools over the Model Context
Protocol (the Day-2 interoperability concept).

Why this exists: the orchestrator already calls `read_calendar_today` /
`read_tasks` in-process. Wrapping them in an MCP server means ANY MCP-aware
client (Claude Desktop, another agent, an IDE) can use the same calendar/tasks
capability without importing our Python — that's the whole point of MCP:
tools as a language-agnostic, transport-decoupled interface.

Design (consistent with the rest of the codebase):
* Thin wrapper, no business logic. The pure functions in `agent/tools/` stay
  the single source of truth; this file only adapts them to the MCP tool schema.
  Their docstrings already said "trivial to reuse from the MCP server later" —
  this is that reuse.
* Read-only surface only. Nothing here can create/modify/delete a calendar event
  or a task, mirroring the read-only OAuth scope (Day-4 security concept).
* Runs over stdio by default (how Claude Desktop & most MCP clients launch a
  server). `FastMCP` handles the JSON-RPC protocol; we just register tools.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# Make `agent.` imports resolve when this is launched directly as a script by an
# MCP client (which sets CWD unpredictably), same pattern as orchestrator.py.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp.server.fastmcp import FastMCP

from agent.tools.calendar_tool import read_calendar_today
from agent.tools.tasks_tool import read_tasks

# Server identity advertised to clients during the MCP handshake.
mcp = FastMCP("concierge-calendar")


@mcp.tool()
def get_calendar_today() -> dict[str, Any]:
    """Get the user's Google Calendar events for TODAY (read-only).

    Returns an `events` list (each with summary, start/end times, location) and a
    `count`. If the calendar isn't authorized or is unreachable, returns an empty
    list plus an `error` string instead of failing — no events today is normal.
    """
    return read_calendar_today()


@mcp.tool()
def get_open_tasks() -> dict[str, Any]:
    """Get the user's open (not-done) tasks from their local task list.

    Returns a `tasks` list (each with title, priority, due date, estimated
    minutes, status, notes) and a `count`. Read-only. On a missing or corrupt
    task file, returns an empty list plus an `error` string instead of failing.
    """
    return read_tasks()


def main() -> None:
    """Run the MCP server over stdio (the transport MCP clients launch by default)."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    # Launch for an MCP client, e.g. a Claude Desktop config entry:
    #   { "command": ".../.venv/bin/python", "args": [".../mcp_server/server.py"] }
    main()

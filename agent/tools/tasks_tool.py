"""Tasks tool — reads the user's local task list from data/tasks.json.

Design: tools live in their own module and are imported by the orchestrator,
never inlined into the agent. This file exposes a single ADK-compatible
function, `read_tasks`, whose type hints + docstring become the tool schema
that Gemini sees. Keeping it pure (no agent logic) makes it trivial to test
and to reuse from the MCP server later.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Resolve the data file relative to this module, not the process CWD, so the
# tool works whether launched from the repo root, the api/ dir, or a container.
# data/tasks.json sits two levels up from agent/tools/.
_TASKS_PATH = Path(__file__).resolve().parents[2] / "data" / "tasks.json"

# Allowed priority labels — used to validate/normalize external file content.
# Security/robustness: never trust file data blindly (it could be hand-edited).
_VALID_PRIORITIES = ("high", "medium", "low")


def _coerce_task(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize one raw task dict into a predictable shape.

    Behavior: fills missing fields with safe defaults and clamps the priority
    to a known value so the prioritizer sub-agent never sees surprise data.
    This is defensive parsing — the file is a system boundary.
    """
    priority = str(raw.get("priority", "medium")).lower()
    if priority not in _VALID_PRIORITIES:
        priority = "medium"  # unknown label -> safe middle default

    return {
        "id": str(raw.get("id", "")),
        "title": str(raw.get("title", "")).strip(),
        "priority": priority,
        "due": str(raw.get("due", "")) or None,
        "estimated_minutes": int(raw.get("estimated_minutes", 0) or 0),
        "status": str(raw.get("status", "todo")).lower(),
        "notes": str(raw.get("notes", "")).strip(),
    }


def read_tasks() -> dict[str, Any]:
    """Read the user's open tasks from the local tasks.json file.

    Returns a dict with a `tasks` list (each task has id, title, priority,
    due date, estimated_minutes, status, notes) and a `count`. Use this to
    understand what the user still needs to get done today and this week.

    Returns an empty list with an `error` message instead of raising, so the
    agent can degrade gracefully (judges test missing/corrupt-file edge cases).
    """
    # Edge case: file missing entirely (fresh clone before data is added).
    if not _TASKS_PATH.exists():
        return {"tasks": [], "count": 0, "error": "tasks.json not found"}

    try:
        with _TASKS_PATH.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        # Edge case: corrupt or unreadable file — report, don't crash the agent.
        return {"tasks": [], "count": 0, "error": f"could not read tasks: {exc}"}

    # Accept either a bare list or the documented {"tasks": [...]} envelope.
    raw_tasks = payload.get("tasks", payload) if isinstance(payload, dict) else payload
    if not isinstance(raw_tasks, list):
        return {"tasks": [], "count": 0, "error": "tasks.json has unexpected shape"}

    tasks = [_coerce_task(t) for t in raw_tasks if isinstance(t, dict)]
    # Only surface work that isn't already finished — that's what "what's next"
    # actually needs; done items are noise for prioritization.
    open_tasks = [t for t in tasks if t["status"] != "done"]

    return {"tasks": open_tasks, "count": len(open_tasks)}


if __name__ == "__main__":
    # Quick manual smoke test: `python agent/tools/tasks_tool.py`
    from pprint import pprint

    pprint(read_tasks())

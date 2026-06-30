"""Orchestrator — the root concierge agent.

Multi-agent design (Day-2 course concept): this root agent owns the tools and
delegates the final wording to the `prioritizer` sub-agent.

Flow for "what's my day look like?":
  1. `get_day_briefing_data` (one tool) reads the calendar + tasks IN PYTHON and
     runs the deterministic `analyze_schedule`, so the reliable facts (next
     event, overcommitted flag, gaps) never depend on the model re-passing data.
  2. The root agent then transfers to the `prioritizer` sub-agent, which turns
     that data into a short, friendly briefing.

Heavy imports (ADK, dotenv) are guarded so the data path stays importable and
testable before `pip install -r requirements.txt` has run.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

# Make `agent.` imports resolve whether this is run as `python -m agent.orchestrator`
# or `python agent/orchestrator.py` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Load variables from the local environment file when python-dotenv is available.
# Live runs need it for the Gemini key; the data-path smoke test does not.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

# AI Studio (not Vertex): ADK / google-genai read GOOGLE_API_KEY. Mirror the key
# if the user stored it as GEMINI_API_KEY, and pin the non-Vertex provider, so the
# agent works regardless of which standard variable name they used.
if os.environ.get("GEMINI_API_KEY") and not os.environ.get("GOOGLE_API_KEY"):
    os.environ["GOOGLE_API_KEY"] = os.environ["GEMINI_API_KEY"]
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "FALSE")

from agent.prioritizer import analyze_schedule, build_prioritizer_agent
from agent.request_context import get_provided_briefing
from agent.tools.calendar_tool import read_calendar_today
from agent.tools.tasks_tool import read_tasks


def get_day_briefing_data() -> dict[str, Any]:
    """Fetch today's calendar events and open tasks and compute the schedule analysis.

    Returns `events`, `tasks`, and `analysis` (next event, overcommitted flag, free
    gaps, ranked tasks), plus any soft `calendar_error` / `tasks_error`. Call this
    once, first, for any question about the user's day, schedule, meetings, or
    priorities — the analysis is computed here so it never depends on the model
    re-passing data.

    Source of the events/tasks: if the caller supplied a per-request briefing (the
    demo page sends the schedule + to-do the user edited on screen), use exactly
    that, so the answer is transparently grounded in what the user entered.
    Otherwise read the real calendar + tasks.json.
    """
    provided = get_provided_briefing()
    if provided is not None:
        events = provided.get("events", [])
        tasks = provided.get("tasks", [])
        return {
            "events": events,
            "tasks": tasks,
            "analysis": analyze_schedule(events, tasks),
            "calendar_error": None,
            "tasks_error": None,
        }

    tasks_payload = read_tasks()
    calendar_payload = read_calendar_today()
    tasks = tasks_payload.get("tasks", [])
    events = calendar_payload.get("events", [])
    return {
        "events": events,
        "tasks": tasks,
        "analysis": analyze_schedule(events, tasks),
        "calendar_error": calendar_payload.get("error"),
        "tasks_error": tasks_payload.get("error"),
    }


# The agent's name is configurable so the persona matches the widget greeting
# (api/main.py owns the same default). Threaded into the instruction below so the
# agent self-identifies consistently in small talk ("what's your name?").
AGENT_NAME = os.environ.get("CONCIERGE_AGENT_NAME", "José")


ORCHESTRATOR_INSTRUCTION = f"""\
Your name is {AGENT_NAME}, a personal daily concierge for a single user.

WHO YOU ARE / WHAT YOU DO — if the user greets you, asks who you are, what you do,
what you can help with, or how to use you, introduce yourself warmly as {AGENT_NAME}
and explain your job in plain language. DO NOT call any tool for this. Cover, in a
few friendly sentences:
- You help them get on top of their day. You read their calendar and to-do list,
  then give a short, prioritized briefing.
- Specifically you: flag the next thing to prep for, warn when the day is
  overcommitted (back-to-back meetings with no breathing room), and suggest which
  task to slot into a free gap.
- You only ever READ their data (read-only) and keep it private.
- Invite them to try: "what's my day look like?"

FOR A REQUEST ABOUT THEIR DAY, schedule, meetings, or what to focus on:
1. Call `get_day_briefing_data` exactly once to fetch today's events and tasks and
   the precomputed analysis. If it returns a `calendar_error`, briefly note that
   the calendar was unavailable and continue with tasks only.
2. Then transfer to the `prioritizer` agent to write the final briefing from that
   data. Do NOT write the briefing yourself.

For other unrelated questions or small talk, answer briefly and do not call any tool.
"""


def build_orchestrator() -> Any:
    """Construct the root concierge agent with its tool and prioritizer sub-agent.

    Lazy ADK import keeps this module importable (and `get_day_briefing_data`
    testable) before dependencies are installed.
    """
    from google.adk.agents import LlmAgent

    return LlmAgent(
        name="concierge_orchestrator",
        model=os.environ.get("CONCIERGE_MODEL", "gemini-2.5-flash"),
        description=(
            "Daily concierge that gathers the user's calendar + tasks and "
            "delegates prioritization to a sub-agent."
        ),
        instruction=ORCHESTRATOR_INSTRUCTION,
        tools=[get_day_briefing_data],
        sub_agents=[build_prioritizer_agent()],
    )


# Lazily-built singleton so importers (the FastAPI app in Step 8) can reuse the
# agent instead of rebuilding it per request.
_root_agent: Any | None = None


def get_root_agent() -> Any:
    """Return the shared root agent, building it on first use."""
    global _root_agent
    if _root_agent is None:
        _root_agent = build_orchestrator()
    return _root_agent


if __name__ == "__main__":
    # Smoke test the data-gathering path only — no ADK / API key required.
    #   python agent/orchestrator.py
    from pprint import pprint

    print(">>> get_day_briefing_data() (calendar gracefully errors without creds):")
    pprint(get_day_briefing_data())

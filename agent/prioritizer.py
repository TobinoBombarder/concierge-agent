"""Prioritizer sub-agent — turns raw schedule + tasks into a short briefing.

This is the second agent in the multi-agent design (orchestrator + prioritizer,
the Day-2 course concept). Split of responsibility:

* `analyze_schedule()` — DETERMINISTIC pure-Python logic. It computes the facts a
  language model is bad at: which event is next, whether the day is overcommitted
  (3+ effectively back-to-back events), and where the free gaps are. Keeping this
  in code (not the prompt) makes the "sharp angle" reliable and unit-testable.
* `build_prioritizer_agent()` — the ADK sub-agent (an `LlmAgent`). It receives the
  raw events, open tasks, and the precomputed analysis, and writes the friendly,
  actionable briefing (headline + 1-2 line prep for the next meeting + task nudges).

The ADK import is lazy (inside the factory) so this module stays importable — and
`analyze_schedule` stays testable — before `pip install -r requirements.txt`.
"""

from __future__ import annotations

import datetime as dt
import os
from typing import Any

# --- Tunables for the "overcommitted day" heuristic -------------------------
# Treat a gap of <= this many minutes between two meetings as "back-to-back"
# (no real breathing room — walking to a room or a bio break doesn't count).
_SHORT_GAP_MINUTES = 15
# A run of this many consecutive back-to-back events flags an overcommitted day.
_OVERCOMMIT_RUN = 3
# Minimum gap worth surfacing as usable focus time for tasks.
_USABLE_GAP_MINUTES = 30
# How many tasks to put in front of the model — enough to prioritize, not bloat.
_TOP_TASKS = 3

_PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def _parse_dt(iso_value: str) -> dt.datetime | None:
    """Parse an RFC3339/ISO string to a timezone-aware datetime, or None.

    Defensive: never raises on malformed input (the values come from the
    Calendar API boundary). Naive results are assumed to be local time.
    """
    if not iso_value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(iso_value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        # Attach the local timezone so comparisons against "now" are valid.
        parsed = parsed.astimezone()
    return parsed


def analyze_schedule(
    events: list[dict[str, Any]] | None,
    tasks: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Compute deterministic scheduling facts from today's events + open tasks.

    Returns a dict the prioritizer agent can rely on without doing arithmetic:
    `next_event`, `overcommitted` (bool), `longest_back_to_back`, `free_gaps`,
    and `top_tasks`. All-day events are ignored for back-to-back/gap math since
    they don't occupy a time slot.
    """
    now = dt.datetime.now().astimezone()

    # Keep only timed events with a parseable start AND end, sorted by start.
    timed: list[tuple[dt.datetime, dt.datetime, dict[str, Any]]] = []
    for event in events or []:
        if not isinstance(event, dict) or event.get("all_day"):
            continue
        start = _parse_dt(str(event.get("start_iso", "")))
        end = _parse_dt(str(event.get("end_iso", "")))
        if start and end:
            timed.append((start, end, event))
    timed.sort(key=lambda item: item[0])

    # Next event = the first one that hasn't finished yet (ongoing counts).
    next_event: dict[str, Any] | None = None
    for start, end, event in timed:
        if end >= now:
            next_event = {
                "summary": event.get("summary", "(no title)"),
                "start": event.get("start", ""),
                "start_iso": event.get("start_iso", ""),
                "location": event.get("location", ""),
                "starts_in_minutes": max(0, int((start - now).total_seconds() // 60)),
            }
            break

    # Longest run of consecutive back-to-back events + usable free gaps.
    longest_run = 1 if timed else 0
    current_run = 1
    free_gaps: list[dict[str, Any]] = []
    for i in range(1, len(timed)):
        prev_end = timed[i - 1][1]
        cur_start = timed[i][0]
        gap_minutes = (cur_start - prev_end).total_seconds() / 60
        if gap_minutes <= _SHORT_GAP_MINUTES:
            current_run += 1
            longest_run = max(longest_run, current_run)
        else:
            current_run = 1
        if gap_minutes >= _USABLE_GAP_MINUTES:
            free_gaps.append(
                {
                    "from": prev_end.strftime("%H:%M"),
                    "to": cur_start.strftime("%H:%M"),
                    "minutes": int(gap_minutes),
                    "after": timed[i - 1][2].get("summary", ""),
                    "before": timed[i][2].get("summary", ""),
                }
            )

    overcommitted = longest_run >= _OVERCOMMIT_RUN

    # Prioritize open tasks: high>medium>low, then earliest due date first.
    ranked = sorted(
        (t for t in (tasks or []) if isinstance(t, dict)),
        key=lambda t: (
            _PRIORITY_ORDER.get(str(t.get("priority", "medium")).lower(), 1),
            str(t.get("due") or "9999-12-31"),
        ),
    )
    top_tasks = [
        {
            "title": t.get("title", ""),
            "priority": t.get("priority", "medium"),
            "due": t.get("due"),
            "estimated_minutes": t.get("estimated_minutes", 0),
        }
        for t in ranked[:_TOP_TASKS]
    ]

    return {
        "now": now.strftime("%H:%M"),
        "event_count": len(timed),
        "next_event": next_event,
        "overcommitted": overcommitted,
        "longest_back_to_back": longest_run,
        "free_gaps": free_gaps,
        "top_tasks": top_tasks,
    }


# The sub-agent's behavior lives in its instruction (this becomes the system
# prompt). It leans on the deterministic analysis above rather than re-deriving
# times, and is told explicitly to never invent events or tasks.
PRIORITIZER_INSTRUCTION = """\
You are the Prioritizer, a focused scheduling assistant inside a daily concierge.
You receive today's calendar events, the user's open tasks, and a precomputed
ANALYSIS object with reliable facts (next_event, overcommitted, free_gaps,
top_tasks). Trust the ANALYSIS for timing and the overcommitted flag — do not
recompute times yourself.

Write a short, plain-language briefing in this shape:
1. One headline line about the day. If `overcommitted` is true, clearly flag that
   the day is back-to-back with no breathing room and suggest protecting one gap.
   If there are no events, say the day is open.
2. A 1-2 line PREP for `next_event`: one concrete thing to do or bring before it,
   inferred from its title. If there is no next event, skip this.
3. Up to three prioritized task nudges from `top_tasks`, and when it helps, slot
   one into a specific `free_gaps` window (e.g. "knock out X in your 13:00-13:45
   gap").

Rules: be concise and warm, use the user's real event/task names, and NEVER
invent events, tasks, times, or details that aren't in the provided data.
"""


def build_prioritizer_agent() -> Any:
    """Construct the ADK prioritizer sub-agent.

    Lazy ADK import keeps this module importable (and `analyze_schedule`
    testable) before dependencies are installed. The orchestrator (Step 5) wires
    this in as a sub-agent and passes it the events, tasks, and analysis.
    """
    from google.adk.agents import LlmAgent

    return LlmAgent(
        name="prioritizer",
        model=os.environ.get("CONCIERGE_MODEL", "gemini-2.5-flash"),
        description=(
            "Turns today's raw calendar events and open tasks into a short, "
            "prioritized briefing with next-meeting prep and an overcommitment flag."
        ),
        instruction=PRIORITIZER_INSTRUCTION,
    )


if __name__ == "__main__":
    # Smoke test the deterministic core with sample data (no deps / no API key).
    #   python agent/prioritizer.py
    from pprint import pprint

    sample_events = [
        {"summary": "Standup", "all_day": False, "start": "09:00", "end": "09:30",
         "start_iso": "2026-06-26T09:00:00+02:00", "end_iso": "2026-06-26T09:30:00+02:00",
         "location": ""},
        {"summary": "Design review", "all_day": False, "start": "09:30", "end": "10:30",
         "start_iso": "2026-06-26T09:30:00+02:00", "end_iso": "2026-06-26T10:30:00+02:00",
         "location": "Room 2"},
        {"summary": "1:1 with mentor", "all_day": False, "start": "10:35", "end": "11:15",
         "start_iso": "2026-06-26T10:35:00+02:00", "end_iso": "2026-06-26T11:15:00+02:00",
         "location": ""},
        {"summary": "Lunch + deep work", "all_day": False, "start": "13:00", "end": "15:00",
         "start_iso": "2026-06-26T13:00:00+02:00", "end_iso": "2026-06-26T15:00:00+02:00",
         "location": ""},
    ]
    sample_tasks = [
        {"title": "Submit capstone writeup", "priority": "high", "due": "2026-06-27",
         "estimated_minutes": 60, "status": "todo"},
        {"title": "Reply to recruiter", "priority": "medium", "due": None,
         "estimated_minutes": 10, "status": "todo"},
    ]
    pprint(analyze_schedule(sample_events, sample_tasks))

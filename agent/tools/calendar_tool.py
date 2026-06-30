"""Calendar tool — reads TODAY's events from Google Calendar (read-only).

Design (same contract as tasks_tool.py): a single ADK-compatible function,
`read_calendar_today`, whose type hints + docstring become the tool schema that
Gemini sees. The function is pure-ish (no agent logic) so it's trivial to test
and to reuse from the MCP server later.

Security (Day 4 concept): read-only OAuth scope only, the OAuth client secret and
the user token live in files referenced by environment variables (never
hardcoded), and the tool degrades gracefully on missing/expired credentials
instead of raising or launching a browser mid-agent-call.

The heavy Google libraries are imported lazily inside the functions so this
module stays importable (and the rest of the service keeps working) even before
`pip install -r requirements.txt` has been run.
"""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
from typing import Any

# Read-only — the agent must never be able to create/modify/delete events.
_SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]

# Credential file locations. Resolve relative to the agent/ package by default,
# but allow overrides via environment variables so deployments can mount secrets
# elsewhere. `credentials.json` = the OAuth client (downloaded from Google Cloud);
# `token.json` = the stored user token produced by the one-time consent flow.
_AGENT_DIR = Path(__file__).resolve().parents[1]  # agent/
_CREDENTIALS_PATH = Path(
    os.environ.get("GOOGLE_OAUTH_CLIENT_FILE", str(_AGENT_DIR / "credentials.json"))
)
_TOKEN_PATH = Path(
    os.environ.get("GOOGLE_OAUTH_TOKEN_FILE", str(_AGENT_DIR / "token.json"))
)

# Cap the result set — a day realistically has a handful of events, and an
# unbounded list is both a cost and a prompt-bloat risk for the model.
_MAX_EVENTS = 50


def _empty(error: str | None = None) -> dict[str, Any]:
    """Build the standard empty payload, optionally with an error note."""
    payload: dict[str, Any] = {
        "events": [],
        "count": 0,
        "date": dt.date.today().isoformat(),
    }
    if error:
        payload["error"] = error
    return payload


def _format_clock(iso_value: str, all_day: bool) -> str:
    """Turn an event start/end into a short human time, defensively.

    Timed events carry an RFC3339 `dateTime`; all-day events carry a bare `date`.
    Never raise on a malformed value — fall back to the raw string.
    """
    if all_day:
        return "all day"
    if not iso_value:
        return ""
    try:
        # `fromisoformat` handles offsets; normalise a trailing 'Z' (UTC) first.
        parsed = dt.datetime.fromisoformat(iso_value.replace("Z", "+00:00"))
        return parsed.astimezone().strftime("%H:%M")
    except ValueError:
        return iso_value  # unexpected shape — surface it rather than crash


def _coerce_event(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize one raw Calendar API event into a predictable, model-friendly shape.

    Defensive parsing — the API response is a system boundary. The prioritizer
    sub-agent relies on `start_iso`/`end_iso` to detect back-to-back events and
    gaps, so those are passed through verbatim alongside the human-readable times.
    """
    start = raw.get("start", {}) or {}
    end = raw.get("end", {}) or {}
    # All-day events use `date`; timed events use `dateTime`.
    all_day = "date" in start and "dateTime" not in start
    start_iso = str(start.get("dateTime") or start.get("date") or "")
    end_iso = str(end.get("dateTime") or end.get("date") or "")

    summary = str(raw.get("summary", "")).strip() or "(no title)"
    return {
        "id": str(raw.get("id", "")),
        "summary": summary,
        "all_day": all_day,
        "start": _format_clock(start_iso, all_day),
        "end": _format_clock(end_iso, all_day),
        "start_iso": start_iso,  # source of truth for overcommitment logic
        "end_iso": end_iso,
        "location": str(raw.get("location", "")).strip(),
    }


def _load_credentials() -> tuple[Any | None, str | None]:
    """Load read-only OAuth credentials non-interactively.

    Returns `(creds, error)`. Refreshes a silently-expired token when a refresh
    token is present; never opens a browser here (that's `authorize()`'s job).
    Any failure is returned as a human-readable error string, not raised.
    """
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
    except ImportError:
        return None, "calendar libraries not installed (run pip install -r requirements.txt)"

    if not _TOKEN_PATH.exists():
        return None, "calendar not authorized yet — run the one-time setup (authorize)"

    try:
        creds = Credentials.from_authorized_user_file(str(_TOKEN_PATH), _SCOPES)
    except (ValueError, OSError) as exc:
        return None, f"could not read calendar token: {exc}"

    # Happy path: still valid.
    if creds and creds.valid:
        return creds, None

    # Graceful expired-token handling: refresh silently if we can.
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception as exc:  # noqa: BLE001 — refresh raises a variety of errors
            return None, f"calendar session expired, please re-authorize: {exc}"
        # Persist the refreshed token so the next call stays non-interactive.
        try:
            _TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
        except OSError:
            pass  # non-fatal: we still have usable in-memory creds this call
        return creds, None

    return None, "calendar credentials invalid — please re-authorize"


def read_calendar_today() -> dict[str, Any]:
    """Read the user's Google Calendar events for TODAY (read-only).

    Returns a dict with an `events` list and a `count`. Each event has: summary,
    start and end (human HH:MM, or "all day"), start_iso/end_iso (machine times),
    whether it's all_day, and location. Use this to understand the user's
    schedule for today — what meetings they have, when, and where the gaps are.

    Degrades gracefully: if the calendar isn't authorized, the token is expired
    and can't refresh, the libraries are missing, or the API call fails, it
    returns an empty `events` list plus an `error` message instead of raising.
    No events today is a normal result (empty list, no error), not a failure.
    """
    creds, error = _load_credentials()
    if error:
        return _empty(error)

    try:
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError
    except ImportError:
        return _empty("calendar libraries not installed (run pip install -r requirements.txt)")

    # Today's window in the user's local timezone, as RFC3339 with offset.
    now = dt.datetime.now().astimezone()
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_day = start_of_day + dt.timedelta(days=1)

    try:
        service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        response = (
            service.events()
            .list(
                calendarId="primary",
                timeMin=start_of_day.isoformat(),
                timeMax=end_of_day.isoformat(),
                singleEvents=True,  # expand recurring events into instances
                orderBy="startTime",
                maxResults=_MAX_EVENTS,
            )
            .execute()
        )
    except HttpError as exc:
        return _empty(f"calendar API error: {exc}")
    except Exception as exc:  # noqa: BLE001 — network/auth errors shouldn't crash the agent
        return _empty(f"could not reach calendar: {exc}")

    raw_events = response.get("items", [])
    events = [_coerce_event(e) for e in raw_events if isinstance(e, dict)]

    payload = _empty()  # reuses date + empty defaults
    payload["events"] = events
    payload["count"] = len(events)
    return payload


def authorize() -> dict[str, Any]:
    """One-time interactive setup: run the OAuth consent flow and save the token.

    Not a model tool — call this once from a terminal (`python ... calendar_tool.py`)
    to grant read-only calendar access. It opens a browser for consent, then
    writes `token.json` so `read_calendar_today` can run non-interactively after.
    """
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        return {"ok": False, "error": "run pip install -r requirements.txt first"}

    if not _CREDENTIALS_PATH.exists():
        return {
            "ok": False,
            "error": (
                f"OAuth client file not found at {_CREDENTIALS_PATH}. Download it "
                "from Google Cloud Console (OAuth client, Desktop app) and set "
                "GOOGLE_OAUTH_CLIENT_FILE if it lives elsewhere."
            ),
        }

    flow = InstalledAppFlow.from_client_secrets_file(str(_CREDENTIALS_PATH), _SCOPES)
    creds = flow.run_local_server(port=0)
    _TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    return {"ok": True, "token_path": str(_TOKEN_PATH)}


if __name__ == "__main__":
    # Manual use:
    #   python agent/tools/calendar_tool.py auth   -> run one-time consent flow
    #   python agent/tools/calendar_tool.py         -> read today's events
    import sys
    from pprint import pprint

    if len(sys.argv) > 1 and sys.argv[1] == "auth":
        pprint(authorize())
    else:
        pprint(read_calendar_today())

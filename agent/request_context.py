"""Per-request briefing context (request-scoped, stateless-friendly).

The demo page lets the user edit their own schedule + to-do list and send it with
each question, so José answers from *exactly* what's on screen — the viewer can
see the source of the answer. That per-request data is threaded to the agent's
`get_day_briefing_data` tool through a `ContextVar`, which is isolated per async
task (one chat request = one task), so concurrent requests never see each other's
data and the service stays stateless. When nothing is provided the tool falls
back to the real calendar + tasks.json, so the CLI and dashboard paths are
unchanged.
"""

from __future__ import annotations

import contextvars
from typing import Any

# None = "no per-request data; use the real calendar + tasks.json".
_provided_briefing: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "provided_briefing", default=None
)


def set_provided_briefing(data: dict[str, Any] | None) -> contextvars.Token:
    """Set the per-request briefing source; returns a token to reset it after."""
    return _provided_briefing.set(data)


def get_provided_briefing() -> dict[str, Any] | None:
    """Return the per-request briefing source, or None if the caller set none."""
    return _provided_briefing.get()


def reset_provided_briefing(token: contextvars.Token) -> None:
    """Restore the previous value once the request is done (use in a finally)."""
    _provided_briefing.reset(token)

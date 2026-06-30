"""FastAPI service — the deployable entrypoint the dashboard chat widget calls.

This is the real entrypoint (run_local.py is just a CLI for testing). It exposes
`POST /chat` for the life-os-prime pop-up chat widget and `GET /health` for the
Cloud Run load balancer.

Request flow for one chat turn:
  1. Parse + validate the body (Pydantic) — a system boundary, never trusted.
  2. `guardrails.check_input()` screens the text (length, injection, type). A
     rejection is returned to the widget AS the assistant's reply so the bubble
     just shows the friendly refusal — no error state needed in the UI.
  3. Run the orchestrator (which transfers to the prioritizer sub-agent) for one
     stateless turn and return its final text as `{ "reply": ... }`.

Stateless by design: each request creates its own in-memory session, so the
service scales horizontally on Cloud Run with no shared state to coordinate.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from agent.guardrails import check_input
from agent.orchestrator import get_root_agent
from api.rate_limit import FixedWindowLimiter

# --- Config -----------------------------------------------------------------
_APP_NAME = "concierge"

# The agent's display identity — single source of truth so the widget greeting,
# the persona, and any future client all stay consistent. Override per-deploy
# without code changes via env (e.g. brand it differently on another dashboard).
AGENT_NAME = os.environ.get("CONCIERGE_AGENT_NAME", "José")
# Shown as the first assistant bubble when the chat opens. Static (no model call),
# so it's instant and free.
GREETING = os.environ.get(
    "CONCIERGE_GREETING",
    f"Hey there, I'm {AGENT_NAME} — how can I help you today?",
)
# A fixed user id is fine: this is a single-user personal concierge with no
# cross-request memory (each turn gets a fresh session below).
_USER_ID = "dashboard-user"

# CORS: only the dashboard origin(s) may call this from a browser. Comma-separated
# env override for prod (the Vercel / tobiasfrancisty domain); localhost for dev.
_DEFAULT_ORIGINS = "http://localhost:3000"
_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("CONCIERGE_ALLOWED_ORIGINS", _DEFAULT_ORIGINS).split(",")
    if origin.strip()
]

# The self-contained public demo page, served at GET / so one Cloud Run URL is a
# valid no-login Project Link. (The page can also be hosted on Vercel instead —
# it falls back to calling this API cross-origin; add that origin to CORS above.)
_STATIC_DIR = Path(__file__).resolve().parent / "static"
_INDEX_HTML = _STATIC_DIR / "index.html"

# Rate limits protect the PUBLIC /chat endpoint and the free-tier Gemini quota.
# Each turn is a model call, so cap per-IP burst AND total throughput. Tunable per
# deploy via env. (Per-process counters — see api/rate_limit.py for the caveat.)
_PER_IP_LIMIT = int(os.environ.get("CONCIERGE_RATE_PER_IP", "10"))
_PER_IP_WINDOW = int(os.environ.get("CONCIERGE_RATE_PER_IP_WINDOW", "60"))
_GLOBAL_LIMIT = int(os.environ.get("CONCIERGE_RATE_GLOBAL", "60"))
_GLOBAL_WINDOW = int(os.environ.get("CONCIERGE_RATE_GLOBAL_WINDOW", "60"))
_ip_limiter = FixedWindowLimiter(_PER_IP_LIMIT, _PER_IP_WINDOW)
_global_limiter = FixedWindowLimiter(_GLOBAL_LIMIT, _GLOBAL_WINDOW)

app = FastAPI(
    title="Concierge Agent",
    description="Daily concierge (Google ADK + Gemini) for the LIFE OS dashboard.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=False,  # no cookies — the widget sends a plain JSON POST
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["Content-Type"],
)


class ChatRequest(BaseModel):
    """One chat turn from the widget.

    Canonical field is `message`; `text` is accepted as an alias because the
    dashboard's existing coach widget posts `{ text }`, so a future shared client
    needs no reshaping. Pydantic enforces the type at the boundary.
    """

    message: str | None = Field(default=None, description="The user's chat message.")
    text: str | None = Field(default=None, description="Alias for `message`.")

    def content(self) -> str:
        """Return whichever field the caller populated (message wins)."""
        return self.message if self.message is not None else (self.text or "")


class ChatResponse(BaseModel):
    """The assistant's reply for one turn.

    `blocked` is True when the guardrail rejected the input; the widget can still
    just render `reply` (the friendly refusal) but may style blocked turns
    differently. `reason` is a machine code for logging/telemetry, never shown.
    """

    reply: str
    blocked: bool = False
    reason: str = ""


async def _run_one_turn(question: str) -> str:
    """Run the orchestrator for a single stateless turn and return its final text.

    Mirrors run_local.ask(): build a runner over the shared root agent, create a
    throwaway session, send one message, and collect the final response. ADK
    imports happen here (not at module load) only via get_root_agent().
    """
    from google.adk.runners import InMemoryRunner
    from google.genai import types

    runner = InMemoryRunner(agent=get_root_agent(), app_name=_APP_NAME)
    session = await runner.session_service.create_session(
        app_name=_APP_NAME, user_id=_USER_ID
    )
    message = types.Content(role="user", parts=[types.Part(text=question)])

    final_text = ""
    async for event in runner.run_async(
        user_id=_USER_ID, session_id=session.id, new_message=message
    ):
        is_final = getattr(event, "is_final_response", None)
        if is_final and event.is_final_response() and event.content and event.content.parts:
            final_text = "".join(part.text or "" for part in event.content.parts)
    return final_text


def _client_ip(request: Request) -> str:
    """Best-effort client IP for rate-limit keying.

    Behind Cloud Run / a proxy the real client is the first hop of
    `X-Forwarded-For`; fall back to the socket peer for local/direct calls.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@app.get("/", include_in_schema=False)
def demo_page() -> FileResponse:
    """Serve the public, no-login demo chat page (the judge-facing Project Link)."""
    return FileResponse(_INDEX_HTML, media_type="text/html")


@app.get("/health")
def health() -> dict[str, Any]:
    """Liveness probe for Cloud Run — cheap, no model call, no auth."""
    return {"status": "ok", "service": _APP_NAME}


@app.get("/greeting")
def greeting() -> dict[str, Any]:
    """Return the agent's name + opening line for the widget to show on open.

    Static and model-free, so the chat bubble can greet instantly the moment it's
    opened without spending a token or waiting on Gemini.
    """
    return {"name": AGENT_NAME, "greeting": GREETING}


@app.post("/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest, request: Request) -> ChatResponse:
    """Handle one chat turn from the widget: rate-limit, guardrail, then run the agent.

    Always returns HTTP 200 with a `reply` the widget can render directly —
    including rate-limit/guardrail refusals (as the assistant's message) and a
    graceful fallback if the model call fails — so the chat bubble never has to
    render a raw HTTP error.
    """
    # Rate limit BEFORE the model call so abuse can't spend quota. Global cap
    # first (protects the shared key), then the per-IP burst cap.
    if not _global_limiter.check("__global__").allowed:
        return ChatResponse(
            reply="I'm getting a lot of requests right now — please try again in a minute.",
            blocked=True,
            reason="rate_limited_global",
        )
    if not _ip_limiter.check(_client_ip(request)).allowed:
        return ChatResponse(
            reply="You're sending messages quickly — give me a moment and try again.",
            blocked=True,
            reason="rate_limited_ip",
        )

    guard = check_input(payload.content())
    if not guard.allowed:
        # Surface the friendly refusal as the assistant's reply (chat UX), and
        # tag it so the widget/telemetry can distinguish a blocked turn.
        return ChatResponse(reply=guard.message, blocked=True, reason=guard.reason)

    try:
        reply = await _run_one_turn(guard.text)
    except Exception as exc:  # noqa: BLE001 — never leak a stack trace to the browser
        # Log full detail server-side; return a generic, safe message to the user.
        print(f"[chat] agent run failed: {exc!r}")
        return ChatResponse(
            reply="Sorry, I hit a problem reaching your assistant. Please try again.",
            blocked=False,
            reason="agent_error",
        )

    return ChatResponse(reply=reply or "(no response)")

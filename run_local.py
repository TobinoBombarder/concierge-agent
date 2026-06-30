"""Local dev runner — drive the concierge agent from the terminal.

Usage (with the venv + GEMINI/GOOGLE key loaded from the env file):
    ./.venv/bin/python run_local.py "what's my day look like?"

Builds the orchestrator (which transfers to the prioritizer sub-agent), sends
one message, and prints the final response. Not part of the deployable service —
the FastAPI app (Step 8) is the real entrypoint; this is just for testing.
"""

from __future__ import annotations

import asyncio
import sys

from google.adk.runners import InMemoryRunner
from google.genai import types

from agent.orchestrator import get_root_agent

_APP = "concierge"
_USER = "local-test"


async def ask(question: str) -> str:
    runner = InMemoryRunner(agent=get_root_agent(), app_name=_APP)
    session = await runner.session_service.create_session(app_name=_APP, user_id=_USER)
    message = types.Content(role="user", parts=[types.Part(text=question)])

    final_text = ""
    async for event in runner.run_async(
        user_id=_USER, session_id=session.id, new_message=message
    ):
        is_final = getattr(event, "is_final_response", None)
        if is_final and event.is_final_response() and event.content and event.content.parts:
            final_text = "".join(part.text or "" for part in event.content.parts)
    return final_text


if __name__ == "__main__":
    question = " ".join(sys.argv[1:]).strip() or "what's my day look like?"
    print(f"\n>>> {question}\n")
    print(asyncio.run(ask(question)) or "(no response)")

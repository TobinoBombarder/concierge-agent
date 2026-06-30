# José — a personal daily concierge agent

**Kaggle 5-Day AI Agents: Intensive Vibe Coding — Capstone (Concierge track)**

---

## The problem

Every morning starts with the same small, annoying question: *"what does my day
actually look like?"* You have a calendar. You have a task list. But neither
answers the question on its own. The calendar shows events but not which one
matters or what you need to prepare. The task list shows everything you *could*
do but not what fits into the gaps you actually have. So you sit there, mentally
stitching the two together, every single morning.

That stitching — *which* meeting is next, *what* to prep for it, *whether* the
day is too packed, *which* task to slide into a free 30 minutes — is judgement
work. It is exactly the kind of small reasoning task that eats attention at the
worst possible time of day.

**José** answers that one question, and answers it well. Ask *"what's my day look
like?"* and José reads today's calendar and your task list, then replies with a
short, friendly briefing: a headline on the shape of the day, a 1–2 line **prep
note for your next meeting**, and a nudge toward your most important tasks. Its
sharp angle: it **flags an overcommitted day** — a run of back-to-back meetings
with no breathing room — and offers to protect a gap.

## Why an agent, and not a script?

It would be tempting to solve this with a template: print the next event, list
the top three tasks, done. But that misses the point. "What should I prepare?"
and "is this day too packed?" and "what fits in this gap?" are reasoning
questions with no fixed answer — they depend on context, phrasing, and
judgement. That is an agent's job: combine **tool use** (go fetch the real
calendar and tasks) with **reasoning** (decide what matters and say it like a
human would).

The design choice I'm proudest of is the **division of labour between the model
and plain Python**. Language models are good at wording and prioritization. They
are unreliable at clock arithmetic — "are these two meetings back-to-back?",
"how long is the gap between 11:15 and 13:00?". So José keeps all the *timing
facts* in deterministic Python (`analyze_schedule()`), unit-testable and never
hallucinated, and lets the model do only what it's good at: turning those facts
into advice. The result is reliable structure with a human voice on top.

## What José does, end to end

1. A message arrives at the FastAPI `/chat` endpoint (from the public demo page,
   the dashboard widget, or the CLI).
2. It passes an **input guardrail** — type and length checks, Unicode
   normalization, and a prompt-injection screen — *before* it reaches the model.
3. It passes a **rate limiter** (per-IP + global) that protects the free-tier
   Gemini quota *before* any model call is made.
4. The **orchestrator agent** (Google ADK `LlmAgent` on Gemini 2.5-flash) calls
   one tool, `get_day_briefing_data`, which reads the calendar (read-only OAuth)
   and `tasks.json`, then runs `analyze_schedule()` in Python to compute the
   next event, the overcommitted flag, free gaps, and ranked tasks.
5. The orchestrator **transfers to the prioritizer sub-agent**, which turns those
   deterministic facts into the short natural-language briefing.
6. The reply comes back as JSON (`{reply, blocked, reason}`) and renders in the
   chat bubble.

If the calendar isn't authorized, José degrades gracefully — it uses the task
list alone and simply notes the calendar was unavailable. Nothing crashes.

## Architecture

```
                        ┌──────────────────────────────────────────────┐
  Browser               │                FastAPI service               │
  ┌───────────────┐ POST │  guardrails.check_input()  ← validate/safety │
  │ Public demo   │─────▶│        │                                     │
  │ page (José)   │ /chat│        ▼                                     │
  └───────────────┘      │  rate limit (per-IP + global) ← quota guard   │
  ┌───────────────┐      │        │                                     │
  │ life-os-prime │─────▶│        ▼                                     │
  │ chat widget   │      │  ┌───────────────── ADK ──────────────────┐  │
  └───────────────┘      │  │ Orchestrator (LlmAgent, Gemini)        │  │
                         │  │   ├─ tool: get_day_briefing_data       │  │
                         │  │   │     ├─ read_calendar_today (RO OAuth)│ │
                         │  │   │     ├─ read_tasks (tasks.json)      │  │
                         │  │   │     └─ analyze_schedule (pure Py)   │  │
                         │  │   └─ transfers to ▼                     │  │
                         │  │ Prioritizer (LlmAgent) → writes briefing│  │
                         │  └─────────────────────────────────────────┘ │
                         └──────────────────────────────────────────────┘

  MCP server exposes the SAME read-only tools (get_calendar_today,
  get_open_tasks) to any MCP client, e.g. Claude Desktop.
```

The orchestrator owns the tools and gathers facts; the prioritizer turns facts
into prose. Keeping the timing logic in `analyze_schedule()` means the two agents
never have to re-derive arithmetic — they reason over numbers Python already got
right.

## Course concepts demonstrated (≥3 required — this shows 4)

| Concept | Where it lives |
|---|---|
| **Multi-agent system (ADK)** | `agent/orchestrator.py` + `agent/prioritizer.py` — a root `LlmAgent` that gathers data and **transfers to** a specialist prioritizer sub-agent. |
| **MCP server** | `mcp_server/server.py` — a FastMCP server exposing the read-only calendar + tasks tools over stdio, so any MCP client (e.g. Claude Desktop) can use the same capabilities. |
| **Security** | `agent/guardrails.py` (input validation + prompt-injection screen), read-only OAuth scope, secrets in env only, and public-demo rate limiting. |
| **Deployability** | Stateless FastAPI + `Dockerfile` → Google Cloud Run, with reproducible deploy steps and a `/health` probe. |

## Security — the heart of the Concierge track

A concierge touches personal data — your calendar, your tasks — so safety isn't a
bolt-on. José is built defensively from the first line:

- **Read-only calendar.** The OAuth scope is hard-pinned to `calendar.readonly`.
  The agent *cannot* create, edit, or delete events even if the model tried.
- **No secrets in code.** The Gemini key, OAuth client, and cached user token all
  load from env vars / files that are gitignored *and* excluded from the Docker
  image via `.dockerignore`. Nothing sensitive enters source control or the
  container. (I verified this with a repo-wide secret scan before going public.)
- **Input guardrail before the model.** Every message runs through
  `check_input()` first: type and length caps, Unicode (NFKC) normalization and
  control-character stripping, and a tight prompt-injection regex set. Refusals
  are deliberately vague so they don't leak the rule that triggered them — and
  the guardrail is tuned to *allow* legitimate phrases like "ignore the 9am
  standup" while blocking real jailbreak attempts.
- **Quota / abuse protection.** The public `/chat` is rate-limited (per-IP and
  global) *before* the model call, so a free-tier key can't be drained by a
  burst of traffic.
- **No data leakage on error.** Failures return a generic message to the user;
  full detail is logged server-side only.

## Deployability

The service is a stateless FastAPI app, so it scales horizontally with no shared
state — a natural fit for Cloud Run. A single command builds and deploys:

```bash
gcloud run deploy concierge-agent \
  --source . \
  --region europe-west1 \
  --allow-unauthenticated \
  --set-env-vars "GEMINI_API_KEY=YOUR_KEY,CONCIERGE_ALLOWED_ORIGINS=https://YOUR_DEMO_ORIGIN"
```

The container listens on `$PORT` (Cloud Run sets it), exposes a cheap
`GET /health` liveness probe with no model call, and serves the public demo page
at `/`. I verified the Docker image builds and runs locally — it boots in ~2
seconds and serves `/health`, `/greeting`, and the demo page before deploy, so
the Cloud Run step carries no surprises.

## How to try it

- **Public demo page** — a no-login chat page served at `/` (José persona,
  responsive, accessible, respects reduced-motion). This is the judge-facing
  link: open it and ask *"what's my day look like?"* or *"who are you?"*.
- **CLI** — `./.venv/bin/python run_local.py "what's my day look like?"`.
- **Dashboard widget** — a bottom-right chat bubble built into my Next.js LIFE OS
  dashboard (`life-os-prime`) that POSTs to the same `/chat` endpoint, showing the
  agent embedded in a real product surface.

Only a free Gemini key is required; calendar access is optional and degrades
gracefully when absent.

## What I learned

- **Push determinism down, push wording up.** The biggest reliability win was
  refusing to let the model do arithmetic. Once `analyze_schedule()` owned "is
  this back-to-back?", the agent's answers stopped drifting and the prioritizer's
  job shrank to something it does reliably: phrasing.
- **Guardrails need a false-positive test, not just a true-positive one.** My
  first injection screen happily blocked "ignore the 9am standup" — a perfectly
  normal thing to say to a concierge. Testing the *allow* path was as important
  as testing the *block* path.
- **Multi-agent is about responsibility, not headcount.** The orchestrator /
  prioritizer split isn't there to look sophisticated; it's there so each agent
  has one clear job — gather facts, or write the briefing — which made each one
  easier to prompt, test, and reason about.
- **Free-tier quotas are a real constraint, so design for them.** Rate-limiting
  *before* the model call, and choosing `gemini-2.5-flash` (which had working
  free quota where 2.0-flash was capped at zero on my key), were practical
  decisions that kept a public demo viable at no cost.

## Possible next steps

Persist a short conversation memory so José can follow up ("move that task to the
gap I mentioned"), add write actions behind an explicit confirmation step
(create a focus block), and broaden the MCP surface so other assistants can drive
the same tools. But the capstone scope was deliberately tight: answer one
question, answer it safely, and answer it well.

---

*Stack: Google ADK · Gemini 2.5-flash · FastAPI · MCP (FastMCP) · Google
Calendar API (read-only) · Docker / Cloud Run. Built standalone so any
front-end — demo page, dashboard widget, or CLI — can call it over HTTP.*

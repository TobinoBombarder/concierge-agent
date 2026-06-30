# José — a personal daily concierge agent

> Kaggle **5-Day AI Agents: Intensive Vibe Coding** capstone — **Concierge track**.

José answers one deceptively simple question — **"what's my day look like?"** —
and answers it *well*. It reads your Google Calendar (today) and your task list,
hands the raw data to a specialist sub-agent, and replies with a short, friendly
briefing: a headline on the day, a 1–2 line **prep for your next meeting**, and a
nudge on your top tasks. Its sharp angle: it **flags an overcommitted day** (a run
of back-to-back meetings with no breathing room) and offers to protect a gap.

It's built to keep personal data safe — read-only calendar access, secrets in env
vars only, and an input guardrail in front of the model — which is the whole point
of the Concierge track.

---

## For reviewers / judges — run it yourself in ~3 minutes

José is a self-contained service you run locally with **your own free Gemini key**
— nothing of mine, no shared quota, no sign-up. The short version (full testing
guide in **[MANUAL.md](MANUAL.md)**):

```bash
# 1. Clone + enter
git clone https://github.com/TobinoBombarder/concierge-agent.git
cd concierge-agent

# 2. Install (needs Python 3.12+)
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt

# 3. Add YOUR free Gemini key — get one at https://aistudio.google.com/apikey
cp .env.example .env          # then edit .env and set GEMINI_API_KEY=...

# 4. Run the demo page + API
./.venv/bin/uvicorn api.main:app --port 8000
# open http://localhost:8000  →  ask "what's my day look like?"
```

The demo page has an **editable Schedule + To-do panel**: change it and ask, and
José answers from exactly what you entered — so the source of every reply is
visible and yours to edit. Only `GEMINI_API_KEY` is required; Google Calendar is
optional and degrades gracefully without it. See **[MANUAL.md](MANUAL.md)** to
also test the prompt-injection guardrail, rate limiting, and the MCP server.

---

## Why an agent?

"What's my day look like?" isn't a database query — it needs **judgement**:
which meeting matters next, what to prep, whether the day is too packed, and which
task to slot into a free gap. That's a reasoning + tool-use problem, exactly what
an agent is for. José splits it into the parts a model is good at (wording,
prioritization) and the parts it's bad at (clock arithmetic, "is this
back-to-back?"), keeping the latter in deterministic Python so the advice is
reliable, not hallucinated.

---

## Architecture

```
                        ┌──────────────────────────────────────────────┐
  Browser               │                FastAPI service               │
  ┌───────────────┐     │                                              │
  │ Public demo   │ POST │  guardrails.check_input()  ← validate/safety │
  │ page (José)   │─────▶│        │                                     │
  │  /  (or Vercel)│ /chat│        ▼                                     │
  └───────────────┘     │  rate limit (per-IP + global) ← quota guard   │
  ┌───────────────┐     │        │                                     │
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

  MCP server (mcp_server/server.py) exposes the SAME read-only tools
  (get_calendar_today, get_open_tasks) to any MCP client, e.g. Claude Desktop.
```

**Multi-agent split:** the orchestrator owns the tools and gathers facts; the
prioritizer turns those facts into the briefing. The reliable timing facts
(next event, overcommitted flag, free gaps) are computed in
`analyze_schedule()` — pure Python, unit-testable — so they never depend on the
model re-deriving arithmetic.

---

## Course concepts demonstrated (≥3 required — this project shows 4)

| Concept | Where |
|---|---|
| **Agent / Multi-agent system (ADK)** | `agent/orchestrator.py` + `agent/prioritizer.py` — root LlmAgent transfers to a prioritizer sub-agent |
| **MCP Server** | `mcp_server/server.py` — FastMCP server exposing the read-only calendar + tasks tools over stdio |
| **Security features** | `agent/guardrails.py` (input validation + prompt-injection screen), read-only OAuth scope, secrets in env only, public-demo rate limiting |
| **Deployability** | Stateless FastAPI + `Dockerfile` → Cloud Run; reproducible steps below |

---

## Project layout

```
concierge-agent/
├── agent/
│   ├── orchestrator.py     # root agent: tool + transfer to prioritizer
│   ├── prioritizer.py      # sub-agent + analyze_schedule() (deterministic facts)
│   ├── guardrails.py       # check_input(): validation + prompt-injection screen
│   └── tools/
│       ├── calendar_tool.py# read-only Google Calendar (today)
│       └── tasks_tool.py   # local tasks.json reader
├── mcp_server/server.py    # MCP wrapper exposing the read-only tools
├── api/
│   ├── main.py             # FastAPI: POST /chat, GET /, /health, /greeting
│   ├── rate_limit.py       # fixed-window limiter (per-IP + global)
│   └── static/index.html   # public, no-login demo chat page
├── data/tasks.json         # sample task list
├── Dockerfile              # Cloud Run image
├── requirements.txt
└── run_local.py            # CLI: drive the agent from the terminal
```

---

## Setup (local)

Requires Python 3.12+.

```bash
cd concierge-agent
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt

# Configure secrets: copy the example env file and add your Gemini key.
cp .env.example .env          # then edit .env, set GEMINI_API_KEY=...
```

Get a free Gemini key at <https://aistudio.google.com/apikey>. Only
`GEMINI_API_KEY` is required; calendar access is optional (without it, José uses
`tasks.json` only and says the calendar was unavailable).

**Run the API + demo page:**

```bash
./.venv/bin/uvicorn api.main:app --port 8000
# open http://localhost:8000  → chat with José
```

**Or drive it from the CLI:**

```bash
./.venv/bin/python run_local.py "what's my day look like?"
```

**Optional — enable read-only Google Calendar:** download a Desktop-app OAuth
client from Google Cloud Console to `credentials.json`, then run the one-time
consent flow (writes `token.json`, which is gitignored):

```bash
./.venv/bin/python agent/tools/calendar_tool.py auth
```

**Optional — run the MCP server** (e.g. add to Claude Desktop's config):

```json
{ "command": "/abs/path/.venv/bin/python",
  "args": ["/abs/path/concierge-agent/mcp_server/server.py"] }
```

---

## Deployment

### Agent service → Google Cloud Run

The service is stateless, so it scales horizontally with no shared state.

```bash
# From concierge-agent/. Builds the image and deploys in one step.
gcloud run deploy concierge-agent \
  --source . \
  --region europe-west1 \
  --allow-unauthenticated \
  --set-env-vars "GEMINI_API_KEY=YOUR_KEY,CONCIERGE_ALLOWED_ORIGINS=https://YOUR_DEMO_ORIGIN"
```

- The container listens on `$PORT` (Cloud Run sets it; defaults to 8080).
- **Secrets:** pass `GEMINI_API_KEY` via `--set-env-vars` or, better,
  Secret Manager (`--set-secrets`). It is **never** baked into the image.
- `GET /health` is a cheap liveness probe (no model call).
- After deploy, the Cloud Run URL itself serves the demo page at `/`.

### Demo page → Vercel (optional, instead of serving from Cloud Run)

The page (`api/static/index.html`) is self-contained static HTML and can be
hosted anywhere. To host it on Vercel pointing at the Cloud Run API:

1. Deploy `api/static/index.html` as a static site on Vercel.
2. Tell it where the API is — set the API base before the page script runs, e.g.
   add to the page `<head>`:
   ```html
   <script>window.CONCIERGE_API_BASE = "https://concierge-xxxx.run.app";</script>
   ```
3. Add the Vercel origin to the service's CORS allow-list:
   `CONCIERGE_ALLOWED_ORIGINS=https://your-demo.vercel.app`.

---

## Security notes (Concierge track)

- **Read-only calendar.** The OAuth scope is hard-pinned to
  `calendar.readonly` — the agent cannot create, edit, or delete events.
- **No secrets in code.** `GEMINI_API_KEY`, OAuth client, and the user token all
  load from env/files that are gitignored and excluded from the Docker image.
- **Input guardrail.** Every message passes `check_input()` first: type + length
  checks, Unicode normalization, and a prompt-injection screen, before it ever
  reaches the model.
- **Abuse / quota protection.** The public `/chat` is rate-limited (per-IP +
  global) *before* the model call, so the free-tier key can't be drained.
- **No data leakage on error.** Failures return a generic message to the user;
  full detail is logged server-side only.

---

## Configuration reference

All optional except `GEMINI_API_KEY`. See `.env.example`.

| Variable | Default | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | — (required) | Gemini / AI Studio key |
| `CONCIERGE_MODEL` | `gemini-2.5-flash` | Model id |
| `CONCIERGE_AGENT_NAME` | `José` | Display name / persona |
| `CONCIERGE_GREETING` | derived | Opening line |
| `CONCIERGE_ALLOWED_ORIGINS` | `http://localhost:3000` | CORS allow-list |
| `CONCIERGE_RATE_PER_IP` / `_WINDOW` | `10` / `60` | Per-IP rate limit |
| `CONCIERGE_RATE_GLOBAL` / `_WINDOW` | `60` / `60` | Global rate limit |
| `GOOGLE_OAUTH_CLIENT_FILE` | `credentials.json` | OAuth client path |
| `GOOGLE_OAUTH_TOKEN_FILE` | `token.json` | Cached token path |

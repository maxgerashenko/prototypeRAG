# prototypeRAG
Voice assistant

- [Project plan](plan/PROJECT_PLAN.md) — what we build: ingestion, RAG, voice calls, actions
- [Development plan](plan/DEVELOPMENT_PLAN.md) — how: three learning stages — local → cloud → scale
- [Architecture drivers](plan/ARCHITECTURE_DRIVERS.md) — principles, wants, and restrictions that force deviations
- [Decisions log](plan/DECISIONS.md) — priorities, decided vs open, options compared for each decision

Detailed plan per part:
1. [Crawler](plan/01-crawler.md) — scrape website + Google listing into chunks
2. [Local RAG](plan/02-local-rag.md) — local LLM (LM Studio) + Postgres/pgvector, same tech as in cloud
3. [Voice channel](plan/03-voice-channel.md) — phone calls via Twilio, STT/TTS
4. [Actions](plan/04-actions.md) — bookings, calendar, conversation summaries
5. [Cloud — stage 3](plan/05-cloud-migration.md) — Cloud Run + Neon + Gemini for one pilot business, only env vars change
6. [Scale — stage 2 multi-business, stage 3 cost/load](plan/06-scale.md) — many businesses: tenant isolation, onboarding, reliability, cost per business
7. [Knowledge quality](plan/07-knowledge-quality.md) — business = domain with locations, organize/clean step, facts library, business summary
8. [Voice tuning](plan/VOICE_TUNING.md) — every setting and feature that affects the speed and quality of replies
9. [Crawler, database & retrieval tuning](plan/CRAWLER_TUNING.md) — every setting and feature that affects crawl speed, how data is stored and retrieved, and answer quality

Layer docs (how the code is built today, one file per layer): [docs/README.md](docs/README.md)

Feature plans: [Pull-to-refresh in the voice app](plan/feature-pull-to-refresh.md) (#124) · [Business settings screen](plan/feature-business-settings.md) (#144–#147) · [Group sessions in Bookings](plan/feature-bookings-sessions.md)

Comparisons: [Crawler vs Scrapy, PDF vs MarkItDown](plan/comparison-scrapy-markitdown.md) (#116)

Working on this repo with Claude Code: see [CLAUDE.md](CLAUDE.md) for the file structure and how architecture discussions are recorded.

## Harness, services and test flows (for agents without file access)

A self-contained summary of how this project is built, run, tested and worked on, so a
reader that can only see this README (for example the daily "PrototypeRAG updates" scheduled
task) knows the stack without opening other files. **Keep it current**: the rule in
[CLAUDE.md](CLAUDE.md) ("Keep the README harness section current") says to update it in the
same change whenever a flow, skill, agent, hook, MCP server, external service, infrastructure
piece or test flow changes. Plans and reasons: [plan/DECISIONS.md](plan/DECISIONS.md);
how each layer is coded: [docs/README.md](docs/README.md).

### Product and stack

Multi-business voice and chat assistant: crawl a business's website → organize into chunks
and facts → RAG over Postgres + pgvector → answer by chat, in the browser (push-to-talk) and
by phone (Twilio) → actions (bookings, call transfer, take a message, call summaries). One
deployment for all businesses, isolated by `business_id` (Row-Level Security comes in stage 3).
Stages: 1 local (now), 2 many businesses, 3 Google Cloud (Cloud Run, Neon, Gemini).

| Part | Tech |
|---|---|
| Backend | Python 3.12, FastAPI + uvicorn (`app/`), SQLAlchemy + Alembic, `uv` for dependencies |
| Layers | `app/ingest` (crawler, organize, facts), `app/rag` (index, retrieve, answer), `app/voice` (session, VAD, STT/TTS, Twilio), `app/actions` (booking, transfer, messages, summary), `app/api` (REST, WebSocket, SSE), `app/insights` |
| Database | Postgres 16 + pgvector in Docker (the only container in day-to-day use); vectors and all app data in one DB, no other store |
| LLM | `openai` client against an OpenAI-compatible endpoint: LM Studio locally, Gemini in cloud (env vars only). No LangChain or LlamaIndex |
| Front end | React 19 + TypeScript, Vite multi-page app in `web/`, built to `web/dist` and served by FastAPI under `/web` |
| Speech | Google Speech-to-Text and Text-to-Speech (Application Default Credentials), also locally; `webrtcvad` for voice activity detection |

### Local models (LM Studio on a 64 GB Mac)

- Chat and voice: `qwen/qwen3.6-35b-a3b` with reasoning off (`LLM_MODEL`, `CHAT_REASONING_EFFORT=none`, `VOICE_REASONING_EFFORT=none`); was `google/gemma-4-12b` until 2026-11-02 (DEC-58).
- Embeddings: `text-embedding-nomic-embed-text-v1.5`, 768 dims (`EMBED_MODEL`, `EMBED_DIM`).
- Code drafting for Claude Code delegation: the same qwen model with thinking on, loaded only when delegating.
- Gemini is the cloud swap; switching is env vars plus a re-index (vectors differ).

### External services

| Service | Used for | Where | Credentials |
|---|---|---|---|
| LM Studio (local) / Gemini (cloud) | chat, voice and fact-extraction LLM, embeddings | `app/llm.py` | `LLM_*`, `EMBED_*` env vars |
| Google Speech-to-Text / Text-to-Speech | voice calls | `app/voice/stt.py`, `tts.py` | `gcloud auth application-default login` (ADC) |
| Twilio | phone number, Voice SDK test calls, webhooks, media stream, SMS | `app/voice/twilio_*.py`, `app/actions/sms.py` | `TWILIO_*`, `PUBLIC_BASE_URL` |
| ngrok | public URL for Twilio webhooks to the local app | started by the SessionStart hook | `PUBLIC_BASE_URL` in `.env` |
| Google Calendar | bookings backend (`BOOKING_BACKEND=google_calendar`), polled every 60 s | `app/actions/booking/` | OAuth token file or service-account key |
| Google Places | business listing data; client code exists, not live-tested and not yet called from the crawl | `app/ingest/places.py` | a Google API key (not set up yet) |
| Anthropic API (optional) | fact extraction when the Haiku choice is picked | `app/ingest/facts.py` | `ANTHROPIC_API_KEY` |

Cost rule (DEC-33): only free or pay-per-use services, no monthly fees. Secrets live in
`.env` (git-ignored); `.env.example` lists every variable.

### Data and infrastructure

- Stage 1: Postgres in Docker (`docker compose up -d --wait`); app, crawler and LM Studio run natively. `docker compose --profile app` also runs migrate + API from the one `Dockerfile`; `docker compose run --rm crawler --url <site>` runs the crawler.
- Schema in `app/db/models.py`, migrations in `app/db/migrations` (`uv run alembic upgrade head`). The dev DB is shared with tests, so leftover test businesses appear in the app; `scripts/prune_businesses.py` cleans them. Never run a downgrade or destructive test on the dev DB; use a throwaway `app_scratch` DB (see "Throwaway database" below).
- Crawled pages, chunks, facts and conversations are stored in Postgres, not files.
- Stage 3 target: Cloud Run + Cloud Run Jobs with `min-instances=0`, Neon Postgres, Gemini; no Cloud SQL.

### Claude Code harness (`.claude/`, `CLAUDE.md`)

- **Rules** live in `CLAUDE.md` (checked in): plan-file table, decision workflow (DEC/OPEN/R ids), delegation rules, commit and PR rules (no AI-attribution trailers or "Generated by" footers; ask before commit, push or PR; PRs say `Fixes #n`).
- **Subagents** (`.claude/agents/`): `Agent_opus_ARCHITECT` (Opus, read-only, plans a feature with steps tagged QWEN / SONNET / CLAUDE / USER), `Agent_sonnet5.5_CODE` (Sonnet, edits files and runs a step's check, never commits), `sonnet5.5_REVIEW` (Sonnet, read-only reviewer of every implemented step).
- **Local-model tools** (`.claude/tools/`, call LM Studio): `delegate_ask.py` (summarize, find, check), `delegate.py` (draft a shell command), `delegate_code.py` (draft one whole file from a spec), `delegate_doc.py` (plan files, issue drafts, PR text). Review, planning, decisions and commit messages are never delegated.
- **Hooks** (`.claude/settings.json`): `SessionStart` runs `session_start.sh` (starts ngrok to `PUBLIC_BASE_URL` and the Pixel Agents office on :3100); `SubagentStart` / `SubagentStop` drive the Pixel Agents characters via `pixel.py`. All exit 0 and never block a session.
- **MCP servers** (`.mcp.json`, project scope, versions pinned): `context7` (live library docs for FastAPI, Twilio, Google APIs, pgvector, Vite), `playwright` (Playwright MCP: drive and screenshot the `web/` pages), `postgres` (Postgres MCP Pro, `--access-mode=restricted` = read-only, pointed only at the throwaway `app_scratch` DB, never at `app`). Cloud threads also use GitHub MCP tools for issues and PRs. Local checks: "Claude Code MCP servers and skills" below.
- **Skills / plugins** (`.claude/settings.json` → `enabledPlugins`): `superpowers@claude-plugins-official` (TDD, debugging, planning and review skills). It overlaps the architect / review flow below; where they disagree, `CLAUDE.md` wins. No project-written skills yet.
- **Flows**: *feature* = architect plan → `plan/feature-<slug>.md` + GitHub issues → implement (qwen or Sonnet) → Sonnet review → fixes → user check → PR. *Small change* = branch, qwen drafts, real check, Sonnet review, user check, PR. Claude-config-only changes go straight to `main`. Work is tracked as GitHub issues in milestones named `S<stage> · M<n> <name>`; labels `area:*`, `type:*`, `blocked`, `in-progress`, `LOCAL_USER_PHONE` (needs the Mac or a real call). Merged PR → close the issue, remove `in-progress`.

### Test flows

| Check | Command | Needs |
|---|---|---|
| Backend tests (about 40 files in `tests/`) | `uv run pytest -q` | Postgres; `tests/test_rag.py` also needs LM Studio |
| Frontend type check and unit tests | `cd web && npm run typecheck && npm test` (Vitest) | Node |
| Browser tests | `cd web && npm run e2e` (Playwright, mocked backend) | Node, `npx playwright install chromium` |
| RAG eval | `uv run python -m app.rag.eval --business-id <id> --file tests/eval/bathhouse.yaml [--retrieval vector\|hybrid]` (default `hybrid` = vector + full-text by RRF, as in production; `vector` = baseline to measure what full-text adds) | DB, LM Studio, an indexed business |
| Pilot flow | ingest `--url …` → `app.rag.index` → eval; hand questions in `questions/` | DB, LM Studio |
| Live checks only the Mac can do | voice call, push-to-talk, Twilio call, Google Calendar sync | LM Studio, Google ADC, Twilio, ngrok; listed under "Test on Mac" in `plan/03-voice-channel.md` |

There is no CI. Cloud sessions have no LM Studio, Google credentials or Twilio, so checks
that need them are not run there and are listed in the PR as not run.

### Claude Code MCP servers and skills (check once on the Mac)

Config: `.mcp.json` and `.claude/settings.json`. Needs Node.js (`npx`), [uv](https://docs.astral.sh/uv/)
(`uvx`) and the compose Postgres running. Nothing here was run when it was added (the cloud
session has no Mac): run these steps once and report any that fail.

1. **Approve the servers.** Start `claude` in the repo, accept the project MCP prompt, then `/mcp`:
   `context7`, `playwright` and `postgres` should all say *connected*. First start downloads the packages.
2. **Context7.** Ask: "use context7 to show the current FastAPI `lifespan` example". It should call
   `resolve-library-id` / `query-docs` and answer from the docs.
3. **Playwright MCP.** Build and serve the app, then ask Claude to open it and screenshot it:
   ```bash
   (cd web && npm ci && npm run build)
   uv run uvicorn app.main:app --timeout-graceful-shutdown 1
   ```
   Ask: "open http://localhost:8000/web/chat.html and describe the page". It should return a snapshot or screenshot.
   If the browser is missing: `npx playwright install chromium`. Mic and Twilio flows still need a human.
4. **Postgres MCP Pro (read-only, scratch DB only).** It is configured for `app_scratch`, so create it first
   (steps in "Throwaway database" above, option a or b), then ask: "list the tables in the database"
   and "show the 5 largest `chunks` by text length". Then confirm it is read-only: ask it to
   `CREATE TABLE x(i int)`; it must be refused. Drop the scratch DB when done. Never change the URI to `app`.
5. **Superpowers.** `/plugin` should list `superpowers` as installed (if it is not, run
   `/plugin install superpowers@claude-plugins-official`). Then `/help` or ask "what superpowers skills are available?"
   and expect names such as brainstorming, test-driven-development, systematic-debugging.
6. **No regressions.** `uv run pytest -q` and `cd web && npm run typecheck && npm test` still pass.

If a server fails to start, `claude mcp list` shows the error. To turn one off for yourself without
touching the repo, list it under `disabledMcpjsonServers` in `.claude/settings.local.json` (not git-ignored yet, so do not commit it).

## Run locally (stage 1)

Needs Docker, [uv](https://docs.astral.sh/uv/) (`brew install uv`) and Node.js 20.19+ or 22.12+
(`brew install node`) for the web pages.

```bash
cp .env.example .env
docker compose up -d --wait       # Postgres 16 + pgvector, the only container
uv sync                           # Python 3.12 venv + dependencies
uv run alembic upgrade head       # create tables
(cd web && npm ci && npm run build)    # React + TS pages -> web/dist (DEC-41); repeat after web/ changes
uv run uvicorn app.main:app --reload --timeout-graceful-shutdown 1   # http://localhost:8000/health, pages under /web
# --timeout-graceful-shutdown: without it a Python change hangs the restart while a page
# holds the live-reload stream (DEV_RELOAD=true) or a voice WebSocket open
uv run pytest                     # needs the DB; tests/test_rag.py also needs LM Studio (chat + embedding model loaded)
docker compose exec postgres psql -U app -d app   # look at the data
```

Pilot business end to end (DEC-31):

```bash
uv run python -m app.ingest.run --url https://www.abathhouse.com/williamsburg   # prints business_id
uv run python -m app.rag.index --business-id <id>                               # embed chunks
uv run python -m app.rag.eval --business-id <id> --file tests/eval/bathhouse.yaml
```

Questions to ask by hand, with expected answers, one file per business: [questions/](questions/).

Then open a page:

| Page | URL (uvicorn, built pages) | Needs |
|---|---|---|
| **Voice app** (DEC-42): pick a business, read past conversations, call with push-to-talk, continue an earlier call | http://localhost:8000/ (redirects to `/web/`; the old `/web/mic-test.html` does too) | DB; for calls also LM Studio, `gcloud auth application-default login`, Chrome or Safari |
| Chat test (answers + retrieved sources; paste a `business_id`) | http://localhost:8000/web/chat.html | DB, LM Studio |
| Voice debug (hands-free: open mic + server VAD, raw event log; paste a `business_id`) | http://localhost:8000/web/voice-debug.html | as for calls, plus headphones |
| Twilio call (mode B: Voice SDK → TwiML App → `/twilio/voice` → `/voice/ws`, billed per minute) | http://localhost:8000/web/call.html (localhost only) | as for calls, plus `TWILIO_*` + `PUBLIC_BASE_URL` in `.env` and ngrok — setup in [03-voice-channel.md](plan/03-voice-channel.md) |

In the voice app, hold **Hold to talk** (or hold Space) while you speak and release to send;
pressing while the assistant talks interrupts it. Space also starts a call (selected
business, conversation list) or continues the open transcript; Esc goes back or ends the
call; Backspace ends a call. Every call someone spoke in is saved and shows up under the
business with a NEW badge.

### LM Studio (stage 1)

LM Studio runs natively on the Mac (Apple GPU; Docker has no GPU access, DEC-28/DEC-29) and
serves an OpenAI-compatible API on `http://localhost:1234/v1`. The app talks to it with the
`openai` client, so the cloud switch to Gemini is env vars only.

Models, split by role so both fit in memory at once (DEC-32, ~39 GB combined on the 64 GB Mac):

| Role | Model id | Used by | Notes |
|---|---|---|---|
| Chat / voice | `qwen/qwen3.6-35b-a3b` (DEC-58; was `google/gemma-4-12b`) | `.env` `LLM_MODEL` | ~29 GB; thinking is on by default, so `.env` sets `CHAT_REASONING_EFFORT=none` and `VOICE_REASONING_EFFORT=none` |
| Embeddings | `text-embedding-nomic-embed-text-v1.5` | `.env` `EMBED_MODEL` | 768 dims; keep it loaded next to the chat model |
| Code drafting (Claude Code delegation only) | `qwen/qwen3.6-35b-a3b`, thinking on | `.claude/tools/delegate_code.py` | ~27-29 GB; not read from `.env`; load it only when delegating |

Setup:

1. Install LM Studio, download the models above from its catalog (names drift, check `lms ls`).
2. Start the server (LM Studio app, Developer tab, or `lms server start`) and load the models
   (`lms load <model>`). Load the chat and embedding models together; `/v1/models` also lists
   models that are downloaded but not loaded, and those fail on demand when memory is full.
3. Check what is loaded with `lms ps` and copy the ids it prints into `.env` (`LLM_MODEL`,
   `EMBED_MODEL`). `LLM_API_KEY` and `EMBED_API_KEY` can be any value.
4. Smoke test: `curl localhost:1234/v1/models`, then `uv run pytest tests/test_rag.py`.

From the Docker image (`--profile app`) the API reaches LM Studio at
`host.docker.internal:1234` (see "Run in Docker" below); turn on "Serve on Local Network" in
LM Studio if the container can't connect.

Memory: macOS lets the GPU use roughly 70-75% of unified memory (~45-48 GB) for weights plus
context, so unload the code-drafting model when you don't need it.

More: [plan/02-local-rag.md](plan/02-local-rag.md) (model choice, memory budget),
[DEC-32](plan/DECISIONS.md) (the split).

### Run in Docker (stage-1 exit, step 7)

The API and crawler also run from one Docker image (`Dockerfile`: Node stage builds
`web/dist`, Python stage installs deps with `uv sync --frozen`). Config is env vars only,
the same ones Cloud Run gets in stage 2; `.env` is read by Compose but never copied into
the image. Everything outside the `app`/`crawler` profiles is unchanged:
`docker compose up -d --wait` still starts only Postgres.

```bash
docker compose --profile app up -d --build --wait   # postgres + migrate (alembic, exits) + api on :8000
curl localhost:8000/health
docker compose run --rm crawler --url https://www.abathhouse.com/williamsburg   # same image
docker compose run --rm api python -m app.rag.index --business-id <id>         # any CLI
docker compose logs -f api
docker compose --profile app down                    # stop; the pgdata volume stays
```

Inside the containers Compose overrides `.env`'s `localhost` values:
`DATABASE_URL` points at the `postgres` service and `LLM_BASE_URL` / `EMBED_BASE_URL` at
LM Studio on the Mac via `host.docker.internal:1234` (LM Studio stays native, R7). Google
STT/TTS use the ADC file from `gcloud auth application-default login`, mounted read-only
from `~/.config/gcloud`; without it the API starts and logs one warning, only calls fail.
Don't run the native `uvicorn` at the same time (both use port 8000). The image listens on
`$PORT` (8000 here, 8080 on Cloud Run).

### Web pages (React + TypeScript, `web/`)

The pages are a Vite multi-page app (DEC-41); FastAPI serves the built `web/dist` under
`/web`. `web/dist` and `web/node_modules` are not committed. If `/web/` returns 404, the
pages haven't been built yet — run `npm run build`.

```bash
cd web
npm ci              # install exact versions from package-lock.json (first time / after pulling)
npm run build       # type-check + build to web/dist; uvicorn serves it without a restart
npm run dev         # hot reload on http://localhost:5173/web/ (also /web/chat.html, /web/voice-debug.html, /web/call.html);
                    # proxies /chat, /debug, /businesses, /twilio, /health, /dev and the /voice WebSocket to :8000,
                    # so keep `uv run uvicorn app.main:app --reload` running alongside
npm run build -- --watch   # alternative: rebuild on save; with DEV_RELOAD=true in .env the pages
                           # served by uvicorn reload themselves (never during a call)
npm run typecheck   # type-check only
npm test            # unit tests (Vitest): formatting + the call state machine
npx playwright install chromium   # once per machine, for the browser tests
npm run e2e         # browser tests (Playwright): builds, serves web/dist on :4173 and runs every
                    # screen of the voice app + both debug pages against a mocked backend
                    # (no DB, LM Studio or Google needed); `npx playwright show-report` for details
```

Code: `web/src/app/` (voice app: one file per screen, `callState.ts` = call state machine,
`useVoiceCall.ts` = push-to-talk wiring), `web/src/voice/voiceCall.ts` (mic + WebSocket +
playback, shared with the debug page), `web/src/chat/`, `web/src/voice-debug/`,
`web/src/api.ts` (backend types), `web/public/dev-reload.js` (DEV_RELOAD), `web/e2e/` (browser
tests; `mocks.ts` = mocked API and voice socket). The backend's read API for the app is
tested in `tests/test_conversations.py` and `tests/test_voice_session.py`.

### Throwaway database (migration round trips, destructive tests)

Never run `alembic downgrade` or a destructive test against the dev DB `app`, and never
copy it with `CREATE DATABASE … TEMPLATE app`. That command waits up to 5 s for every
other session on `app` (uvicorn, pytest, a psql shell) to leave, and new connections to
`app` block behind it, including the compose healthcheck's `pg_isready -d app`. When the
healthcheck gives up after its 3 s timeout, the postmaster sees a server process die and
restarts all of Postgres ("server process … exited with exit code 2 / terminating any
other active server processes"). Reproduced on `pgvector/pgvector:pg16` 16.15: it happens
with the healthcheck and does not without it. Without the crash the copy still fails
whenever anything stays connected to `app`, so use one of these instead:

```bash
P="docker compose exec -T postgres"
$P dropdb -U app --if-exists --force app_scratch && $P createdb -U app app_scratch

# then ONE of a) or b):
# a) empty schema (default): just the migrations, plus whatever fixture the test inserts
DATABASE_URL=postgresql+psycopg://app:app@localhost:5432/app_scratch uv run alembic upgrade head

# b) a copy of the dev data, when the test needs real rows (pg_dump reads a snapshot,
#    so it is safe while the app is connected to `app`)
$P pg_dump -U app app | $P psql -q -v ON_ERROR_STOP=1 -U app -d app_scratch

# round trip against the throwaway DB only
export DATABASE_URL=postgresql+psycopg://app:app@localhost:5432/app_scratch
uv run alembic downgrade base && uv run alembic upgrade head && uv run alembic current
unset DATABASE_URL

$P dropdb -U app --force app_scratch     # clean up; --force closes leftover sessions
```

`DATABASE_URL` in the environment wins over `.env` (pydantic-settings), so the same
override points `uv run pytest` or the app at `app_scratch`.

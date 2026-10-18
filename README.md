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
5. [Cloud — stage 2](plan/05-cloud-migration.md) — Cloud Run + Neon + Gemini for one pilot business, only env vars change
6. [Scale — stage 3](plan/06-scale.md) — many businesses: tenant isolation, onboarding, reliability, cost per business
7. [Knowledge quality](plan/07-knowledge-quality.md) — business = domain with locations, organize/clean step, facts library, business summary

Working on this repo with Claude Code: see [CLAUDE.md](CLAUDE.md) for the file structure and how architecture discussions are recorded.

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

Then open a page:

| Page | URL (uvicorn, built pages) | Needs |
|---|---|---|
| **Voice app** (DEC-42): pick a business, read past conversations, call with push-to-talk, continue an earlier call | http://localhost:8000/ (redirects to `/web/`; the old `/web/mic-test.html` does too) | DB; for calls also LM Studio, `gcloud auth application-default login`, Chrome or Safari |
| Chat test (answers + retrieved sources; paste a `business_id`) | http://localhost:8000/web/chat.html | DB, LM Studio |
| Voice debug (hands-free: open mic + server VAD, raw event log; paste a `business_id`) | http://localhost:8000/web/voice-debug.html | as for calls, plus headphones |

In the voice app, hold **Hold to talk** (or hold Space) while you speak and release to send;
pressing while the assistant talks interrupts it. Space also starts a call (selected
business, conversation list) or continues the open transcript; Esc goes back or ends the
call; Backspace ends a call. Every call someone spoke in is saved and shows up under the
business with a NEW badge.

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
npm run dev         # hot reload on http://localhost:5173/web/ (also /web/chat.html, /web/voice-debug.html);
                    # proxies /chat, /debug, /businesses, /health, /dev and the /voice WebSocket to :8000,
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

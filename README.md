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

Working on this repo with Claude Code: see [CLAUDE.md](CLAUDE.md) for the file structure and how architecture discussions are recorded.

## Run locally (stage 1)

Needs Docker, [uv](https://docs.astral.sh/uv/) (`brew install uv`) and Node.js 20.19+ or 22.12+
(`brew install node`) for the web pages.

```bash
cp .env.example .env
docker compose up -d --wait       # Postgres 16 + pgvector, the only container
uv sync                           # Python 3.12 venv + dependencies
uv run alembic upgrade head       # create tables
(cd web && npm ci && npm run build)    # React + TS pages -> web/dist (DEC-37); repeat after web/ changes
uv run uvicorn app.main:app --reload   # http://localhost:8000/health, pages under /web
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
| **Voice app** (DEC-38): pick a business, read past conversations, call with hold-to-talk | http://localhost:8000/ (redirects to `/web/`) | DB; for calls also LM Studio, `gcloud auth application-default login`, Chrome or Safari |
| Chat test (answers + retrieved sources; paste a `business_id`) | http://localhost:8000/web/chat.html | DB, LM Studio |
| Voice mic test (mode C, open mic + raw event log; paste a `business_id`) | http://localhost:8000/web/mic-test.html | as for calls, plus headphones |

In the voice app, hold **Hold to talk** (or Space/Enter with the button focused) while you
speak and release to send; pressing while the assistant talks interrupts it. Every call is
saved and shows up under the business with a NEW badge.

### Web pages (React + TypeScript, `web/`)

The pages are a Vite multi-page app (DEC-37); FastAPI serves the built `web/dist` under
`/web`. `web/dist` and `web/node_modules` are not committed. If `/web/` returns 404, the
pages haven't been built yet — run `npm run build`.

```bash
cd web
npm ci              # install exact versions from package-lock.json (first time / after pulling)
npm run build       # type-check + build to web/dist; uvicorn serves it without a restart
npm run dev         # hot reload on http://localhost:5173/web/ (also /web/chat.html, /web/mic-test.html);
                    # proxies /chat, /debug, /businesses and the /voice WebSocket to uvicorn on :8000,
                    # so keep `uv run uvicorn app.main:app --reload` running alongside
npm run typecheck   # type-check only
npm test            # unit tests (Vitest): formatting + the call state machine
npx playwright install chromium   # once per machine, for the browser tests
npm run e2e         # browser tests (Playwright): builds, serves web/dist on :4173 and runs every
                    # screen of the voice app + both debug pages against a mocked backend
                    # (no DB, LM Studio or Google needed); `npx playwright show-report` for details
```

Code: `web/src/app/` (voice app: one file per screen, `callState.ts` = call state machine,
`useVoiceCall.ts` = hold-to-talk wiring), `web/src/voice/voiceCall.ts` (mic + WebSocket +
playback, shared with the mic test), `web/src/chat/`, `web/src/mic-test/`, `web/src/api.ts`
(backend types), `web/e2e/` (browser tests; `mocks.ts` = mocked API and voice socket).
The backend's read API for the app is tested in `tests/test_conversations.py`.

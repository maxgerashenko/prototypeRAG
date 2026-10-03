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

Needs Docker and [uv](https://docs.astral.sh/uv/) (`brew install uv`).

```bash
cp .env.example .env
docker compose up -d --wait       # Postgres 16 + pgvector, the only container
uv sync                           # Python 3.12 venv + dependencies
uv run alembic upgrade head       # create tables
uv run uvicorn app.main:app --reload   # http://localhost:8000/health
uv run pytest                     # foundation tests (need the DB running)
docker compose exec postgres psql -U app -d app   # look at the data
```

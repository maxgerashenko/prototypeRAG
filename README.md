# prototypeRAG
Voice assistant

- [Project plan](plan/PROJECT_PLAN.md) — what we build: ingestion, RAG, voice calls, actions
- [Development plan](plan/DEVELOPMENT_PLAN.md) — how: local Docker stack first, then cheap Google Cloud deployment
- [Architecture drivers](plan/ARCHITECTURE_DRIVERS.md) — principles, wants, and restrictions that force deviations
- [Decisions log](plan/DECISIONS.md) — priorities, decided vs open, options compared for each decision

Detailed plan per part:
1. [Crawler](plan/01-crawler.md) — scrape website + Google listing into chunks
2. [Local RAG](plan/02-local-rag.md) — local LLM (Ollama) + Postgres/pgvector, same tech as in cloud
3. [Voice channel](plan/03-voice-channel.md) — phone calls via Twilio, STT/TTS
4. [Actions](plan/04-actions.md) — bookings, calendar, conversation summaries
5. [Move to cloud](plan/05-cloud-migration.md) — Cloud Run + Neon (later Cloud SQL) + Gemini, only env vars change

Working on this repo with Claude Code: see [CLAUDE.md](CLAUDE.md) for the file structure and how architecture discussions are recorded.

# CLAUDE.md

Prototype of a multi-business voice/chat assistant: crawl a business's website → RAG over
Postgres + pgvector → answer by chat and phone (Twilio) → actions (bookings, summaries).
Built locally first (Ollama, Docker Postgres), then moved to Google Cloud (Cloud Run,
Neon → Cloud SQL, Gemini) by changing env vars only.

The project is currently in the **planning stage** — no application code yet.

## Plan files (`plan/`, flat — no subfolders)

| File | Holds | Update when |
|---|---|---|
| `PROJECT_PLAN.md` | What we build: overview of the 4 parts, milestones | Scope or milestones change |
| `DEVELOPMENT_PLAN.md` | How: stack table local ↔ cloud, principles, env vars, code structure, phases | Stack or phases change |
| `ARCHITECTURE_DRIVERS.md` | Drivers D1–D7 (principles + priority), wants W1–W9, restrictions R1–R20 that force deviations | A hard limit is found that bends a driver |
| `DECISIONS.md` | Priorities, build order, decision summary DEC-xx, options tables, open questions OPEN-xx, facts behind decisions | Any decision is made, changed or opened |
| `01-crawler.md` … `05-cloud-migration.md` | Detailed plan per part: goal, done-when, tech, code layout, tasks | Details of that part change |

`README.md` links to all plan files — keep it in sync when files are added or renamed.

## Workflow for architecture discussions

When the user discusses architecture or pastes external advice (e.g. from Gemini):

1. **Evaluate against the existing plan first.** Read `DECISIONS.md` and
   `ARCHITECTURE_DRIVERS.md`. Check the advice against the drivers and priority order;
   say clearly what is correct, what is wrong (with the fact), and what conflicts with a
   decision already made. Don't adopt advice just because it's offered.
2. **Record the outcome** — every discussion that changes or confirms something ends in
   the files, not only in chat:
   - New decision → add a row to the summary table in `DECISIONS.md` and a `DEC-xx`
     section with an options table (option · pros · cons · verdict ✅/❌/🔄).
   - Changed decision → mark the old one **Superseded by DEC-yy**; never delete history.
   - Unresolved question → add `OPEN-xx` (options, depends on, needed before).
     When resolved, remove it from the open table and create a `DEC-xx`.
   - New hard limit (law, vendor terms, latency, quality, cost) that forces a deviation →
     add `R-xx` to `ARCHITECTURE_DRIVERS.md` (why unavoidable · which driver it breaks ·
     forced choice · status) and update its summary table (section 4).
   - Corrected fact or wrong external claim → add a row to "Knowledge from the
     discussion" in `DECISIONS.md`.
3. **Propagate the decision** into the affected part files and overview files so no file
   contradicts another. After editing, grep for the replaced technology/term across
   `plan/` and `README.md` to find leftovers (e.g. `grep -rn -i "qdrant" plan README.md`).
4. **Keep IDs stable.** DEC/OPEN/R/D/W numbers are referenced across files; append new
   numbers, don't renumber.

## Current decisions to respect (see `DECISIONS.md` for reasons)

- One deployment for all businesses; isolation via `business_id` + Postgres Row-Level Security.
- One database: Postgres + pgvector for vectors and all app data. No Qdrant/Firestore.
- LLM calls through the `openai` client against OpenAI-compatible endpoints
  (Ollama locally, Gemini in cloud). No LangChain/LlamaIndex.
- Google Speech APIs for STT/TTS, also locally. Native Google SDKs only in the voice module.
- Crawled pages and chunks stored in Postgres, not files. Crawler in its own Docker image.
- Cloud Run + Cloud Run Jobs; Neon free tier first, Cloud SQL when live.
- Priority when goals conflict: correctness & legal > caller experience > multi-tenancy >
  same tech local/cloud > simplicity > cost > local first.

## Writing style for plan files

- English, concise, tables for comparisons, checklists (`- [ ]`) for tasks.
- Link between plan files with relative links (`[DECISIONS.md](DECISIONS.md)`).
- Prices, free-tier limits and model names change — mark them as approximate and
  re-check before decisions depend on them.

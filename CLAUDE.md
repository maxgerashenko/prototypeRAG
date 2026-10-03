# CLAUDE.md

Prototype of a multi-business voice/chat assistant: crawl a business's website → RAG over
Postgres + pgvector → answer by chat and phone (Twilio) → actions (bookings, summaries).
Built locally first (LM Studio on a 64 GB Mac, Docker Postgres), then moved to Google Cloud (Cloud Run,
Neon → Cloud SQL, Gemini) by changing env vars only.

Built in three learning stages (DEC-27): **1 Local** — understand the principles;
**2 Cloud** — understand the infrastructure with one pilot business; **3 Scale** — many
businesses, safety, reliability, price. Each stage uses the simplest solution that reaches
its goal; harder problems are deferred to the stage where they become real.

Stage 1 build is in progress — see the build order in `DECISIONS.md` for what exists.

## Plan files (`plan/`, flat — no subfolders)

| File | Holds | Update when |
|---|---|---|
| `PROJECT_PLAN.md` | What we build: overview of the 4 parts, milestones | Scope or milestones change |
| `DEVELOPMENT_PLAN.md` | How: stack table local ↔ cloud, principles, env vars, code structure, phases | Stack or phases change |
| `ARCHITECTURE_DRIVERS.md` | Drivers D1–D7 (principles + priority), wants W1–W9, restrictions R1–R20 that force deviations | A hard limit is found that bends a driver |
| `DECISIONS.md` | Priorities, build order, decision summary DEC-xx, options tables, open questions OPEN-xx, facts behind decisions | Any decision is made, changed or opened |
| `01-crawler.md` … `04-actions.md` | Detailed plan per feature part (built in stage 1): goal, done-when, tech, code layout, tasks | Details of that part change |
| `05-cloud-migration.md` | Stage 2: move to Google Cloud with one pilot business | Cloud setup changes |
| `06-scale.md` | Stage 3: many businesses — isolation, onboarding, reliability, cost, ops | Scale topics change |

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
  (LM Studio locally, Gemini in cloud). No LangChain/LlamaIndex.
- Google Speech APIs for STT/TTS, also locally. Native Google SDKs only in the voice module.
- Voice: pipeline mode (STT → LLM → TTS) locally and in cloud; Gemini Live compared in
  stage 2. RAG is a tool (`search_business_info`) shared by both modes (DEC-30).
- Local models in LM Studio (64 GB Mac): Qwen3-30B-A3B primary, Gemma 3 27B comparison,
  thinking off for chat/voice.
- Crawled pages and chunks stored in Postgres, not files. Crawler in its own Docker image.
- Cloud Run + Cloud Run Jobs; Neon free tier in stage 2; Neon paid vs Cloud SQL in stage 3.
- Stage 1: only Postgres in Docker, app/crawler/LM Studio native (DEC-28). `business_id`
  everywhere from stage 1; RLS enforced in stage 3 before the second business.
- When recording a decision or open question, say which stage it belongs to.
- Priority when goals conflict: correctness & legal > caller experience > multi-tenancy >
  same tech local/cloud > simplicity > cost > local first.

## Delegating commands to the local model

For **write git** (add/commit/branch), **read-only database** (SELECT, `\d`, `\dt`) and
**write database** (INSERT/UPDATE/DELETE) commands on this repo: draft the command with
the user's local LM Studio model instead of drafting it yourself, then run the result.
Read-only git (status/log/diff/show) is not in scope — draft those directly as usual.

1. Check which model is actually loaded: `lms ps` (`/v1/models` also lists
   available-but-unloaded models, which fail to load on demand if others already fill
   memory).
2. **Ask the user before delegating each specific task** — this is a standing workflow,
   not permission to go silent; confirm per task, every session.
3. Draft via `python3 .claude/tools/delegate.py "<task in plain English>" [model-id]`
   (pass `model-id` when the loaded model differs from the script's default). It talks
   to LM Studio at `http://localhost:1234/v1` and prints one command, or a line starting
   `REFUSE:` if the task was destructive/ambiguous.
4. Run the printed command yourself — review it first like any other command; a local
   model drafting it doesn't exempt it from the usual checks before destructive ops.
   Execute it directly without re-pasting the draft back to the user first.

## Writing style for plan files

- English, concise, tables for comparisons, checklists (`- [ ]`) for tasks.
- Link between plan files with relative links (`[DECISIONS.md](DECISIONS.md)`).
- Prices, free-tier limits and model names change — mark them as approximate and
  re-check before decisions depend on them.

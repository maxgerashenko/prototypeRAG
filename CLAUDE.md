# CLAUDE.md

Prototype of a multi-business voice/chat assistant: crawl a business's website → RAG over
Postgres + pgvector → answer by chat and phone (Twilio) → actions (bookings, summaries).
Built locally first (LM Studio on a 64 GB Mac, Docker Postgres), then moved to Google Cloud (Cloud Run,
Neon, Gemini) by changing env vars only.

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
| `ARCHITECTURE_DRIVERS.md` | Drivers D1–D7 (principles + priority), wants W1–W9, restrictions R1–R21 that force deviations | A hard limit is found that bends a driver |
| `DECISIONS.md` | Priorities, build order, decision summary DEC-xx, options tables, open questions OPEN-xx, facts behind decisions | Any decision is made, changed or opened |
| `01-crawler.md` … `04-actions.md` | Detailed plan per feature part (built in stage 1): goal, done-when, tech, code layout, tasks | Details of that part change |
| `05-cloud-migration.md` | Stage 2: move to Google Cloud with one pilot business | Cloud setup changes |
| `06-scale.md` | Stage 3: many businesses — isolation, onboarding, reliability, cost, ops | Scale topics change |
| `07-knowledge-quality.md` | Business = domain with locations, organize/clean step, facts library, business summary (built in stage 1, before step 5) | Fact/summary/organize design changes |

`README.md` links to all plan files — keep it in sync when files are added or renamed.

## Commit messages carry implementation context

There's no separate changelog file — git history is the implementation log. Write
commit messages that a future agent can rely on without re-reading the diff: file by
file, and *why*, including any bug caught and fixed during review that the diff alone
wouldn't explain (the crawler commit's message is a good example of the level of
detail to aim for). `DECISIONS.md` stays the place for *what was decided and why*;
commit messages are the place for *what changed and why*, per change.

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
- Local models in LM Studio (64 GB Mac), split by role so both fit in memory at once
  (DEC-32): `google/gemma-4-12b` for chat/voice LLM calls (`.env`'s `LLM_MODEL`);
  `qwen/qwen3.6-35b-a3b` (thinking on) for whole-file code-drafting delegation — see
  below.
- Crawled pages and chunks stored in Postgres, not files. Crawler in its own Docker image.
- Cloud Run + Cloud Run Jobs, `min-instances=0` always (DEC-35); Neon free tier, then Neon Launch
  (usage-based). No Cloud SQL.
- Cost model (DEC-33): only free or pay-per-use services — no monthly fees, minimums or trials
  that turn paid. Test calls via Twilio Voice SDK, no phone number until a business pays (DEC-34).
- Stage 1: only Postgres in Docker, app/crawler/LM Studio native (DEC-28). `business_id`
  everywhere from stage 1; RLS enforced in stage 3 before the second business.
- When recording a decision or open question, say which stage it belongs to.
- Priority when goals conflict: correctness & legal > caller experience > multi-tenancy >
  same tech local/cloud > simplicity > cost > local first.

## Delegating to the local model

Simple work goes to the user's local LM Studio model (`google/gemma-4-12b`, the loaded
chat model); **review and planning always stay with Claude** — code review, reviewing
any draft (including the local model's), plans, architecture decisions, plan-file and
commit-message writing, and editing code.

| Kind | Examples | Tool | Ask the user first? |
|---|---|---|---|
| **Trivial** | Read & summarize a file or command output; find something (which file/line, a value); check a result (did tests pass, did a command succeed); read-only commands (`git status/log/diff/show`, `ls`, DB `SELECT`, `\d`, `\dt`) | `delegate_ask.py` (summarize/find/check), `delegate.py` (draft a command) | **No** — just do it |
| **Write commands** | Write git (add/commit/branch); write DB (INSERT/UPDATE/DELETE) | `delegate.py` | **Yes, per task, every session** |
| **Whole-file code drafts** | A Python module from a written spec | `delegate_code.py` (next section) | Standing choice |
| **Never delegated** | Review, planning, decisions, plan files, commit messages, code edits | — | — |

1. Check which model is actually loaded: `lms ps` (`/v1/models` also lists
   available-but-unloaded models, which fail to load on demand if others already fill
   memory).
2. **Summarize / find / check:**
   `python3 .claude/tools/delegate_ask.py "<question>" [file ...] [--model ID]`, or pipe
   output in: `uv run pytest -q 2>&1 | python3 .claude/tools/delegate_ask.py "Did the tests pass?"`.
   Prints a short answer (`PASS`/`FAIL` + the decisive line for checks, exact quotes for
   finds, `NOT FOUND` if absent). Thinking off, ~1–3 s per call.
3. **Draft a command:** `python3 .claude/tools/delegate.py "<task in plain English>" [model-id]`
   prints one command, or a line starting `REFUSE:` if the task was destructive/ambiguous.
   Run it yourself after reviewing it like any other command; a local model drafting it
   doesn't exempt it from the usual checks before destructive ops. Execute it directly
   without re-pasting the draft back to the user first.
4. **Answers are hints, not proof.** Verify yourself anything that a commit, push,
   delete or decision depends on (e.g. read the pytest summary line before committing),
   and read code yourself before editing it — a summary isn't enough to edit from.
5. Not for: large inputs that need full understanding (input is cut at 60k characters),
   or anything where a wrong answer would be silent and costly — do those yourself.

## Delegating whole-file code drafts

A bigger scope than the command delegation above: drafting a whole Python module from
a written spec, for the agent to then review, test, and fix before accepting. This was
first used ad hoc for `app/ingest/*.py` (stage 1 step 2) and is now a standing choice,
not a one-off.

- **Model: `qwen/qwen3.6-35b-a3b`, thinking left ON** (don't pass
  `enable_thinking=False`) — not `qwen3-coder-next` despite its name. Validated
  head-to-head on `app/ingest/discover.py`'s spec: the non-thinking coder model wrote
  `same_domain()` using `str.lstrip('www.')` as if it stripped a prefix (it strips a
  character set, silently wrong on domains like `wonderful.com`); the thinking-enabled
  run reasoned through the same trap explicitly in its `reasoning_content` and avoided
  it, with zero bugs found on review. Reasoning is why: `qwen3-coder-next`'s benchmark
  strength comes from agentic RL with *execution* feedback (edit → run → see failure →
  retry), which this single-shot, no-tool-loop delegation never exercises; thinking
  substitutes for that missing feedback loop. It also matches DEC-29/`02-local-rag.md`'s
  existing rule that reasoning is fine for offline, non-latency-critical jobs — this is
  one. Re-check this choice if it stops holding up on later files; one head-to-head
  isn't proof for every case.
- `qwen/qwen3.6-35b-a3b` is ~27 GB resident — check `lms ps` first and unload other
  models to fit it (same memory-budget note as DEC-29's 64 GB Mac).
- Draft via `uv run python .claude/tools/delegate_code.py <spec_file> [model-id] [--web]`
  (`uv run`: it needs the `openai` package; `--web` drafts one self-contained HTML page
  instead of a Python module). Several drafts can run in parallel (LM Studio serves 4).
  For a change to existing files, put the current file content in the spec and ask for
  the full new file; review it as a diff. Prints the source only (the model's `reasoning_content` isn't surfaced by this
  script; read it directly via the `openai` client if you want to inspect it for a
  specific draft).
- **Still review and test every draft against something real** before accepting it —
  fixtures, a live DB, or the actual target site, matching the workflow used in stage 1
  step 2. A good reasoning trace lowers the bug rate; it doesn't replace verification.

## Task summaries for delegated / parallel work

Whenever a delegated task finishes — a subagent (Sonnet, Opus, …), a local-model draft,
or any of several tasks run in parallel — write one short Markdown summary for it, right
when its result arrives:

- Path: `tmp/agent-tasks/<MM-DD>_<HH-MM>_<who>_<task-slug>.md` — no year, local time the task finished to the minute (`tmp/` is git-ignored —
  these are working notes, not project history; commit messages and `plan/` stay the
  record).
- Content: title line `# Task: <name> — <who> — ✅ done / 🔄 in progress / ❌ failed`,
  what was asked, the result (files changed, decisions, numbers, test results), problems
  found in review, and follow-ups. Link files instead of pasting them; keep it short.
- Update the same file when the task continues (review fixes, a second round) instead of
  creating a new one.
- Subagents don't write these themselves — the session that delegated and reviewed the
  work writes them, so the summary includes the review.

## Ask before submitting

Any agent working in this repo (Claude Code or otherwise) must ask for explicit
confirmation before submitting a change — `git commit`, `git push`, or opening a pull
request — even when the task that produced the change clearly implied it should be
saved. Finishing the work and submitting it are two separate steps: say what would be
committed (files touched, one-line summary) and wait for a yes before running the
command. This holds every time, not just the first time in a session.

## Writing style for plan files

- English, concise, tables for comparisons, checklists (`- [ ]`) for tasks.
- Link between plan files with relative links (`[DECISIONS.md](DECISIONS.md)`).
- Prices, free-tier limits and model names change — mark them as approximate and
  re-check before decisions depend on them.

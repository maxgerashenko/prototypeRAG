---
name: review-conversations
description: Go through recorded chat/voice conversations, find the bot's problems, turn each problem into a scenario test, fix it, and mark the conversation (verified / in testing / fixed) so the UI shows it. Use when the user asks to review, audit or re-check conversations or to re-run the tests on verified ones.
---

# Review conversations

Status of a conversation lives in `conversations.review_status` (shown as a badge in the web UI):

| Status | Meaning |
|---|---|
| none (NULL) | not checked yet |
| `verified` | read in full, no bot problem found. **Not checked again** unless the user asks |
| `in_testing` | has a problem; a scenario test now reproduces it |
| `fixed` | the problem was fixed and its scenario test passes |

Helper: `uv run python .claude/tools/review_conversations.py` (`list`, `show <id>`, `set <id> <status> --note …`, `junk`, `delete … --yes`).

## Loop

1. **Pick the work.** `list` (default: unchecked). If the user said "include verified" / "re-run verified", use `--status all` and re-check those too. Never touch `verified` ones otherwise.
2. **Junk first.** `junk` prints `JUNK` (no user message at all: no signal) and `REVIEW` (caller spoke but got no reply, or hung up within seconds: probably a bot failure, P1, so read it, never delete it). Show the JUNK list to the user and **ask before `delete … --yes`** (irreversible; refuses when a booking references the call).
3. **Read each remaining conversation** with `show <id>` (transcript **and** `action_log`, since tool results are not stored in messages). For long lists run subagents in parallel on batches; the reading and the verdict stay with Claude/Sonnet, not the local model. Check against the known problem classes:
   - **P2** claims "booked"/"cancelled" without a tool result starting with `Booked`/`Cancelled`; repeats a read-back as if done; books without a read-back and a clear yes; invented placeholder names.
   - **P3** speech-to-text garbled session names not recognised; ambiguous names answered with an error or a full list instead of one yes/no guess.
   - **P5** long replies, bullet lists, repeating the full list or facts the caller already has.
   - **P6** asks again for something already given (name, session, time).
   - **P8** wrong date or year.
   - **P1** empty, cut or out-of-sync replies; caller has to repeat themselves.
   - any other wrong, invented or unhelpful answer (compare against the business profile/chunks).
4. **Verdict per conversation.**
   - No problem → `set <id> verified --note "checked <date>"`.
   - Problem → write a scenario (step 5), then `set <id> in_testing --note "<problem id + one line>"`.
5. **Scenario test.** Add a scenario to `tests/scenarios/scenarios.yaml` (see `tests/scenarios/README.md`): a gemma caller persona that reproduces what the real caller said (keep the STT-garbled wording), plus a deterministic assertion (DB state, `action_log`, or one of the invariants). Run it with `uv run pytest -q -m scenario -k <id>`; it must **fail on the current code** (otherwise the test doesn't capture the problem: fix the test). Runs are not parallel.
6. **Fix** the bot (code or prompt) per the repo rules (small change by Claude or qwen; review with `sonnet5.5_REVIEW`; ask before committing).
7. **Mark fixed.** When the scenario passes in 2 runs (models are non-deterministic): `set <id> fixed --note "<what changed>"`. If it still fails, leave `in_testing`.
8. **Record.** Update the plan file / GitHub issue for the problem class in the same pass (repo rule: a tested finding updates both).

## Re-testing verified conversations

Only when asked: `list --status verified`, re-read them against the current code/prompt (and replay with `tests/scenarios` where a scenario exists). If a new problem shows up, move it to `in_testing` and continue at step 5; otherwise leave `verified`.

## Do not

- delete without the user's yes; commit/push without asking; mark `fixed` without a passing scenario; re-check `verified` unasked.

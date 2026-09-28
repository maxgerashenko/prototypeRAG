# Part 6 — Stage 3: Scale to many businesses (safety, reliability, price)

**Goal:** serve many businesses from one deployment — no data leaks between them, calls
that stay fast under load, and a known cost per business that is below what we charge.

**Starts when:** stage 2 exit criteria are met ([05-cloud-migration.md](05-cloud-migration.md))
and a second business is about to be onboarded.

**Done when:**
- New businesses are onboarded without code changes; tenant isolation tests pass.
- Cost per business is measured and below the price charged.
- Call latency stays within budget under a load test.

Principle: introduce each item **when its trigger appears**, in the order below. Nothing
here is built "just in case".

---

## 1. Tenant isolation — hard gate before the second business

Trigger: second business. Decision: DEC-04, restriction R18.

- Migration: `app_user` (non-owner) role, `ENABLE` + `FORCE ROW LEVEL SECURITY` on all
  tables, policy `business_id = current_setting('app.business_id')::uuid`.
- App switches `DATABASE_URL` to `app_user`; migrations use `ADMIN_DATABASE_URL` (owner,
  direct connection).
- No code rewrite: queries already run through `tenant_session(business_id)` since stage 1.
- Tests: business A can never read or write business B's rows (API, voice, actions,
  dashboard, crawler).

## 2. Onboarding without code

Trigger: second business. Details in [04-actions.md](04-actions.md) (dashboard).

1. Owner login (Google sign-in or magic link — OPEN-12).
2. Create business, enter website URL (+ Google `place_id`) → crawler job runs.
3. Owner confirms/edits the business profile (hours, address, booking rules) — source of
   truth for stored facts (DEC-11).
4. Assign a Twilio number → `business_id`; generate greeting audio (DEC-17).
5. Owner tests answers, adds custom replies, enables tools, connects calendar (OAuth, R14).

## 3. Cost visibility and pricing

Trigger: first paying business.

- Record per conversation: call minutes (telephony), STT/TTS seconds, LLM tokens,
  embedding calls → cost per business per month.
- Fixed costs (database, min-instances, number rental) split across businesses.
- Use it to set pricing (per month + per minute?) and to decide the cost reductions in §7.
- Budget alerts per project; anomaly alert if one business's usage spikes.

## 4. Call reliability under load

Trigger: real call traffic from several businesses. Decisions: DEC-16, DEC-25, DEC-26.

- `min-instances=1` for the service handling calls (no cold starts during calls).
- Load test with real call audio → set `--concurrency` (start ≈10–20 per vCPU, OPEN-15)
  and `--max-instances` (also protects DB connections, R20).
- Split into two services from the same image when justified (DEC-26):
  **voice** (Twilio webhook + WebSocket, warm, low concurrency) and **web** (chat,
  dashboard, scale to zero).
- Latency dashboard per stage (VAD, STT, retrieval, LLM, TTS) with alerts.

## 5. Operations

Trigger: more than one person deploying, or deploys becoming frequent/risky.

- CI/CD: GitHub Actions → tests → build both images → deploy (Workload Identity
  Federation, no JSON keys) — OPEN-13.
- Terraform for project, services, jobs, secrets, scheduler.
- `staging` and `prod` environments; Neon branches make staging databases cheap.
- Error alerts, uptime check on the Twilio webhook, failed crawl alerts.

## 6. Database growth

Trigger: Neon free-tier limits, measured wake-up latency on calls (R3), or need for
GCP-native IAM/backups. Decision: DEC-03, OPEN-10.

| Option | When |
|---|---|
| Neon paid (always-on compute or longer autosuspend) | Keep same provider, remove wake-ups |
| Cloud SQL for PostgreSQL | Want everything in GCP (IAM, one bill, HA) |

- Move = `pg_dump`/restore + new `DATABASE_URL`/`ADMIN_DATABASE_URL`; with Cloud SQL add
  `--add-cloudsql-instances` and the Cloud SQL Client role.
- Connection pooling rules (R20); backups and restore test.
- HNSW index on `chunks.embedding` only when exact search per business gets slow.

## 7. Cost reduction

Trigger: cost per business (§3) too close to the price.

| Lever | Expected effect | Decision/open item |
|---|---|---|
| Gemini Live instead of STT → LLM → TTS | Fewer services, lower latency, possibly cheaper | OPEN-08 |
| Telephony provider per country | Per-minute price differences | OPEN-09 |
| Smaller prompts (top 5 chunks, short history), cached business profile | Fewer tokens per turn | DEC-12 |
| Cheaper TTS voice tier | Lower speech cost | DEC-13 |
| Scale web service to zero, keep only voice warm | Lower fixed cost | DEC-26 |
| Neon vs Cloud SQL | Fixed DB cost | OPEN-10 |

## 8. Compliance

Trigger: first business in a regulated market / before go-live.

- Region by target market (OPEN-02): Cloud Run, database, Gemini/Vertex (OPEN-07).
- Retention policy + scheduled cleanup of transcripts and personal data; deletion on request (R16).
- Recordings: opt-in per business with disclosure (DEC-22, OPEN-14, R15).
- Calendar OAuth tokens encrypted, key in Secret Manager (R14).
- Licence decision before any public release (OPEN-11).

## Tasks

- [ ] RLS migration + `app_user` role + isolation tests
- [ ] Owner login + onboarding flow
- [ ] Usage metering per conversation → cost per business report
- [ ] `min-instances=1` + load test → concurrency / max-instances
- [ ] Split voice / web services (if justified)
- [ ] CI/CD + Terraform + staging
- [ ] Database move decision (Neon paid vs Cloud SQL)
- [ ] Cost-reduction experiments (Gemini Live, telephony provider)
- [ ] Retention jobs, region choice, recordings policy

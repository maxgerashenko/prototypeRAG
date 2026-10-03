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
- One shared database stays the default (DEC-04, re-checked). A second database only as
  a **cell per region** (Cloud Run + DB) or for a contract that requires it — OPEN-16.

## 2. Onboarding without code

Trigger: second business. Details in [04-actions.md](04-actions.md) (dashboard).

1. Owner login (Google sign-in or magic link — OPEN-12).
2. Create business, enter website URL (+ Google `place_id`) → crawler job runs.
3. Owner confirms/edits the business profile (hours, address, booking rules) — source of
   truth for stored facts (DEC-11).
4. Assign a Twilio number → `business_id` (its monthly rental is charged to the business, DEC-34); generate greeting audio (DEC-17).
5. Owner tests answers, adds custom replies, enables tools, connects calendar (OAuth, R14).

## 3. Cost visibility and pricing

Trigger: first paying business.

- Record per conversation: call minutes (telephony), STT/TTS seconds, LLM tokens,
  embedding calls → cost per business per month.
- Fixed costs: none shared (DEC-33); each business's number rental is passed to that business.
- Use it to set pricing (per month + per minute?) and to decide the cost reductions in §7.
- Budget alerts per project; anomaly alert if one business's usage spikes.

## 4. Call reliability under load

Trigger: real call traffic from several businesses. Decisions: DEC-35, DEC-25, DEC-26.

- `min-instances=0`; cold start during ringing (DEC-35). Always-on only as a DEC-33 exception.
- Load test with real call audio → set `--concurrency` (start ≈10–20 per vCPU, OPEN-15)
  and `--max-instances` (also protects DB connections, R20).
- Split into two services from the same image when justified (DEC-26):
  **voice** (Twilio webhook + WebSocket, low concurrency) and **web** (chat,
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
more storage, or first paying client (OPEN-17). Decision: DEC-03, DEC-33.

| Option | When |
|---|---|
| **Neon Launch** (usage-based, no monthly minimum) | Free-tier limits reached |
| Cloud SQL for PostgreSQL (1 dedicated vCPU ≈ $50/month) | 🔄 OPEN-17 — once a paying client covers the fixed cost; removes wake-up delay |

- Neon Launch: plan upgrade inside Neon — no move, same `DATABASE_URL`.
- Cloud SQL: `pg_dump`/restore + new `DATABASE_URL` (portability rules in DEC-03).
- Connection pooling rules (R20); backups and restore test.
- HNSW index on `chunks.embedding` only when exact search per business gets slow.

## 7. Cost reduction

Trigger: cost per business (§3) too close to the price.

| Lever | Expected effect | Decision/open item |
|---|---|---|
| Voice mode (pipeline vs Gemini Live, decided in stage 2) | Re-check cost per minute at volume | OPEN-08, DEC-30 |
| Telephony provider per country | Per-minute price differences | OPEN-09 |
| Smaller prompts (top 5 chunks, short history), cached business profile | Fewer tokens per turn | DEC-12 |
| Cheaper TTS voice tier | Lower speech cost | DEC-13 |

## 8. Compliance

Trigger: first business in a regulated market / before go-live.

- Region by target market (OPEN-02): Cloud Run, database, Gemini/Vertex (OPEN-07).
  Clients in a second region → second cell, not a database per business (OPEN-16).
- Retention policy + scheduled cleanup of transcripts and personal data; deletion on request (R16).
- Recordings: opt-in per business with disclosure (DEC-22, OPEN-14, R15).
- Calendar OAuth tokens encrypted, key in Secret Manager (R14).
- Licence decision before any public release (OPEN-11).

## Tasks

- [ ] RLS migration + `app_user` role + isolation tests
- [ ] Owner login + onboarding flow
- [ ] Usage metering per conversation → cost per business report
- [ ] Load test (incl. cold start during ringing) → concurrency / max-instances
- [ ] Split voice / web services (if justified)
- [ ] CI/CD + Terraform + staging
- [ ] Neon free → Launch when limits are reached
- [ ] Cost-reduction experiments (voice mode at volume, telephony provider)
- [ ] Retention jobs, region choice, recordings policy

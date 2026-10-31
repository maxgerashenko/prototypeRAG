// Pure helpers for the Business settings screen (unit-tested in settings.test.ts).

import type { TwilioNumber } from "../api";
import { fmtPhone } from "./format";

/** The fixed first sentence of every call (DEC-53); the owner's text comes after it. */
export const GREETING_PREFIX = "Hi, I am an AI assistant.";

export type GreetingKind = "saving" | "dirty" | "cached" | "notCached";

export interface GreetingView {
  kind: GreetingKind;
  chip: string;
  note: string;
  saveLabel: string;
  saveDisabled: boolean;
  dirty: boolean;
  count: number;
}

/** "" without a time, "just now" under a minute, else "Oct 28, 4:12 PM" (local time). */
export function cachedLabel(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const ageMs = now.getTime() - d.getTime();
  if (ageMs < 60_000) return "just now"; // also a clock a little behind the server's
  const day = d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  const time = d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
  return `${day}, ${time}`;
}

/** Chip, note and button of the greeting card. Precedence: saving > dirty > cached > not cached.
 * An empty draft is allowed: it resets the greeting to the disclosure alone. */
export function greetingView(a: {
  saved: string;
  draft: string;
  cached: boolean;
  saving: boolean;
  cachedAt?: string | null;
  now?: Date;
}): GreetingView {
  const dirty = a.draft !== a.saved;
  const count = a.draft.length;
  const saveDisabled = a.saving || (!dirty && a.cached);
  if (a.saving) {
    return { kind: "saving", chip: "Caching…", note: "Generating audio…", saveLabel: "Saving…", saveDisabled, dirty, count };
  }
  if (dirty) {
    return { kind: "dirty", chip: "Unsaved changes", note: "Save to re-cache", saveLabel: "Save & cache", saveDisabled, dirty, count };
  }
  if (a.cached) {
    const when = cachedLabel(a.cachedAt, a.now);
    return { kind: "cached", chip: "Cached", note: when ? `Cached ${when}` : "Cached", saveLabel: "Saved", saveDisabled, dirty, count };
  }
  return {
    kind: "notCached", chip: "Not cached", note: "Generated live on each call", saveLabel: "Cache greeting", saveDisabled, dirty, count,
  };
}

/** The open card's row hint: "+1 (555) 000-1234 · Greeting cached". `cached` null = not loaded yet. */
export function settingsHint(phoneNumbers: string[], cached: boolean | null): string {
  const num = phoneNumbers.length ? fmtPhone(phoneNumbers[0]!) : "No number";
  return cached === null ? num : `${num} · Greeting ${cached ? "cached" : "not cached"}`;
}

/** "Voice & SMS", "Voice", "SMS" or "". */
export function capsLabel(n: Pick<TwilioNumber, "voice" | "sms">): string {
  return [n.voice && "Voice", n.sms && "SMS"].filter(Boolean).join(" & ");
}

/** Second line of a number: the friendly name when it has letters (Twilio has no city), then "Twilio · Voice & SMS". */
export function numberSubtitle(n: Pick<TwilioNumber, "friendly_name" | "voice" | "sms">): string {
  const name = /\p{L}/u.test(n.friendly_name) ? n.friendly_name : "";
  return [name, "Twilio", capsLabel(n)].filter(Boolean).join(" · ");
}

/** The picker's list: a query with digits matches the number's digits, else the friendly name.
 * Order: this business's own, free ones, then those another business holds. */
export function filterNumbers(list: TwilioNumber[], query: string, bizId: string): TwilioNumber[] {
  const q = query.trim().toLowerCase();
  const digits = q.replace(/\D/g, "");
  const kept = list.filter((n) => {
    if (!q) return true;
    if (digits && !/[a-z]/.test(q)) return n.number.replace(/\D/g, "").includes(digits);
    return n.friendly_name.toLowerCase().includes(q);
  });
  const rank = (n: TwilioNumber) => (n.business_id === bizId ? 0 : n.business_id ? 2 : 1);
  return kept
    .map((n, i) => ({ n, i }))
    .sort((a, b) => rank(a.n) - rank(b.n) || a.i - b.i)
    .map((x) => x.n);
}

/** Text of the sheet's button. */
export function assignLabel(pick: TwilioNumber | null, currentNumber: string | null, assigning: boolean): string {
  if (assigning) return "Assigning…";
  if (!pick) return "Select a number";
  if (pick.number === currentNumber) return "Currently assigned";
  if (pick.business_id) return `Move from ${pick.business_name ?? "another business"}`;
  return `Assign ${fmtPhone(pick.number)}`;
}

export function canAssign(pick: TwilioNumber | null, currentNumber: string | null, assigning: boolean): boolean {
  return !!pick && !assigning && pick.number !== currentNumber;
}

/** CSS width of a question's bar, clamped to 0-100%. */
export function barWidth(percent: number): string {
  const p = Number.isFinite(percent) ? Math.min(100, Math.max(0, percent)) : 0;
  return `${p}%`;
}

/** "12 questions · 5 calls · 30 days" */
export function questionsSummary(total: number, conversations: number, days: number): string {
  return `${total} questions · ${conversations} calls · ${days} days`;
}

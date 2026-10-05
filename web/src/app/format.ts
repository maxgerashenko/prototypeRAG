// Pure formatting helpers for the voice app (unit-tested in format.test.ts).

import type { ConversationSummary } from "../api";

/** Seconds -> "m:ss", or "h:mm:ss" from one hour. */
export function fmtClock(totalSeconds: number): string {
  const sec = Math.max(0, Math.floor(totalSeconds));
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  const ss = (s < 10 ? "0" : "") + s;
  if (h) return `${h}:${m < 10 ? "0" : ""}${m}:${ss}`;
  return `${m}:${ss}`;
}

/** "CB" for "Corner Bakery", "AB" for "abathhouse", "?" for an empty name. */
export function monogram(name: string): string {
  const words = name.match(/[\p{L}\p{N}]+/gu) ?? [];
  if (!words.length) return "?";
  const [first, second] = words as [string, ...string[]];
  const letters = second ? first[0] + second[0] : first.slice(0, 2);
  return letters.toUpperCase();
}

function sameDay(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

/** "Just now", "Today · 9:14 AM", "Yesterday · 6:40 PM", "Sep 28 · 11:02 AM", "Sep 28, 2025 · …" (local time). */
export function fmtWhen(iso: string, now: Date = new Date()): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const ageMs = now.getTime() - d.getTime();
  if (ageMs >= 0 && ageMs < 60_000) return "Just now";
  const time = d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  let day: string;
  if (sameDay(d, now)) day = "Today";
  else if (sameDay(d, yesterday)) day = "Yesterday";
  else
    day = d.toLocaleDateString("en-US", {
      month: "short",
      day: "numeric",
      ...(d.getFullYear() !== now.getFullYear() ? { year: "numeric" } : {}),
    });
  return `${day} · ${time}`;
}

/** Call length: ended_at - started_at, else up to the last message, else 0. */
export function durationSeconds(c: Pick<ConversationSummary, "started_at" | "ended_at">, lastMessageAt?: string): number {
  const start = Date.parse(c.started_at);
  const end = Date.parse(c.ended_at ?? lastMessageAt ?? c.started_at);
  if (Number.isNaN(start) || Number.isNaN(end)) return 0;
  return Math.max(0, Math.round((end - start) / 1000));
}

/** Seconds of `iso` since the conversation started — the "m:ss" next to each bubble. */
export function offsetSeconds(startedAt: string, iso: string): number {
  const s = Date.parse(startedAt);
  const t = Date.parse(iso);
  if (Number.isNaN(s) || Number.isNaN(t)) return 0;
  return Math.max(0, Math.round((t - s) / 1000));
}

export function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** Business card subtitle count: "No chats yet", "1 chat", "3 chats". */
export function chatsLabel(n: number): string {
  return n === 0 ? "No chats yet" : plural(n, "chat", "chats");
}

/** Card preview: the last message, "You: " when the caller said it. */
export function previewText(c: Pick<ConversationSummary, "preview" | "preview_role">): string {
  if (!c.preview) return "No messages";
  return (c.preview_role === "user" ? "You: " : "") + c.preview;
}

/** Search like the design: name or category contains the query (case-insensitive). */
export function matchesQuery(b: { name: string; category: string }, query: string): boolean {
  const q = query.trim().toLowerCase();
  return !q || b.name.toLowerCase().includes(q) || b.category.toLowerCase().includes(q);
}

// Pure formatting helpers for the voice app (unit-tested in format.test.ts).

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

export function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** Business card subtitle count: "No chats yet", "1 chat", "3 chats". */
/** Hint under the Conversations control on the open card. */
export function conversationsHint(n: number): string {
  return n === 0 ? "None yet" : `All ${plural(n, "conversation", "conversations")}`;
}

export function chatsLabel(n: number): string {
  return n === 0 ? "No chats yet" : plural(n, "chat", "chats");
}

/** Card subtitle host like main's page: the business domain, else the website's host
 * without "www.", else "No website". */
export function hostOf(b: { website: string | null; domain?: string | null }): string {
  if (b.domain) return b.domain;
  if (!b.website) return "No website";
  try {
    return new URL(b.website).host.replace(/^www\./, "") || "No website";
  } catch {
    return "No website";
  }
}

/** Card preview: the API already prefixes "You: " when the caller spoke last. */
export function previewText(preview: string): string {
  return preview || "No messages";
}

/** Search: name or host contains the query (case-insensitive). */
export function matchesQuery(b: { name: string; website: string | null; domain?: string | null }, query: string): boolean {
  const q = query.trim().toLowerCase();
  return !q || b.name.toLowerCase().includes(q) || hostOf(b).toLowerCase().includes(q);
}

/** "+1 (555) 000-1234" for a US/Canada E.164 number; anything else as given. */
export function fmtPhone(e164: string): string {
  const m = /^\+1(\d{3})(\d{3})(\d{4})$/.exec(e164);
  return m ? `+1 (${m[1]}) ${m[2]}-${m[3]}` : e164;
}

const OUTCOME_LABELS: Record<string, string> = {
  booked: "Booked",
  info_request: "Info request",
  message: "Message",
  transferred: "Transferred",
  unresolved: "Unresolved",
};

/** Badge text for a call outcome; null when there is none. Unknown values are shown as sent. */
export function outcomeLabel(outcome: string | null | undefined): string | null {
  if (!outcome) return null;
  return OUTCOME_LABELS[outcome] ?? outcome;
}

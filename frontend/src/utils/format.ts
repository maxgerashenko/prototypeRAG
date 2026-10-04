// Pure display-formatting helpers, ported verbatim (same behavior, same copy text)
// from web/mic-test.html's vanilla-JS helpers of the same names.

export function initials(name: string | null | undefined): string {
  const words = (name ?? "").split(/\s+/).filter((w) => /[A-Za-z0-9]/.test(w));
  if (words.length === 0) return "?";
  return words
    .slice(0, 2)
    .map((w) => w[0].toUpperCase())
    .join("");
}

export function hostOf(website: string | null | undefined): string {
  if (!website) return "No website";
  try {
    return new URL(website).host.replace(/^www\./, "");
  } catch {
    return "No website";
  }
}

export function countLabel(n: number): string {
  if (n === 0) return "No chats yet";
  if (n === 1) return "1 chat";
  return n + " chats";
}

export function dateLabel(iso: string, now: Date = new Date()): string {
  const d = new Date(iso);
  const time = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  if (d.toDateString() === now.toDateString()) return "Today · " + time;
  const yest = new Date(now);
  yest.setDate(yest.getDate() - 1);
  if (d.toDateString() === yest.toDateString()) return "Yesterday · " + time;
  return d.toLocaleDateString([], { month: "short", day: "numeric" }) + " · " + time;
}

export function fmtDur(sec: number | null | undefined): string {
  const total = Math.max(0, Math.round(sec || 0));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return m + ":" + (s < 10 ? "0" : "") + s;
}

import { describe, expect, it } from "vitest";
import {
  chatsLabel,
  durationSeconds,
  fmtClock,
  fmtWhen,
  matchesQuery,
  monogram,
  offsetSeconds,
  plural,
  previewText,
} from "./format";

// dates are built in local time, so the tests pass in any time zone
const local = (y: number, mo: number, d: number, h: number, mi: number, s = 0) =>
  new Date(y, mo - 1, d, h, mi, s).toISOString();
const NOW = new Date(2026, 9, 4, 15, 0); // Oct 4 2026, 3:00 PM local

describe("fmtClock", () => {
  it.each([
    [0, "0:00"],
    [5, "0:05"],
    [84, "1:24"],
    [600, "10:00"],
    [3599, "59:59"],
    [3600, "1:00:00"],
    [3725, "1:02:05"],
    [-3, "0:00"],
    [12.9, "0:12"],
  ])("%s s -> %s", (sec, out) => expect(fmtClock(sec)).toBe(out));
});

describe("monogram", () => {
  it.each([
    ["Corner Bakery", "CB"],
    ["Bloom & Stem", "BS"],
    ["Happy Paws Grooming", "HP"],
    ["abathhouse", "AB"],
    ["  ", "?"],
    ["Ñandú Café", "ÑC"],
    ["7-Eleven", "7E"],
  ])("%s -> %s", (name, out) => expect(monogram(name)).toBe(out));
});

describe("fmtWhen", () => {
  it("is 'Just now' within a minute", () => {
    expect(fmtWhen(local(2026, 10, 4, 14, 59, 30), NOW)).toBe("Just now");
  });
  it("names today and yesterday with a 12-hour time", () => {
    expect(fmtWhen(local(2026, 10, 4, 9, 14), NOW)).toBe("Today · 9:14 AM");
    expect(fmtWhen(local(2026, 10, 3, 18, 40), NOW)).toBe("Yesterday · 6:40 PM");
  });
  it("uses month and day this year, plus the year otherwise", () => {
    expect(fmtWhen(local(2026, 9, 28, 11, 2), NOW)).toBe("Sep 28 · 11:02 AM");
    expect(fmtWhen(local(2025, 12, 31, 23, 5), NOW)).toBe("Dec 31, 2025 · 11:05 PM");
  });
  it("handles future and invalid input", () => {
    expect(fmtWhen(local(2026, 10, 4, 16, 0), NOW)).toBe("Today · 4:00 PM");
    expect(fmtWhen("not a date", NOW)).toBe("");
  });
});

describe("durationSeconds / offsetSeconds", () => {
  const start = "2026-10-01T09:00:00Z";
  it("prefers ended_at, then the last message, else 0", () => {
    expect(durationSeconds({ started_at: start, ended_at: "2026-10-01T09:04:12Z" })).toBe(252);
    expect(durationSeconds({ started_at: start, ended_at: null }, "2026-10-01T09:01:00Z")).toBe(60);
    expect(durationSeconds({ started_at: start, ended_at: null })).toBe(0);
    expect(durationSeconds({ started_at: "bad", ended_at: null })).toBe(0);
  });
  it("never goes negative", () => {
    expect(durationSeconds({ started_at: start, ended_at: "2026-10-01T08:00:00Z" })).toBe(0);
    expect(offsetSeconds(start, "2026-10-01T08:59:00Z")).toBe(0);
    expect(offsetSeconds(start, "2026-10-01T09:00:41.6Z")).toBe(42);
  });
});

describe("labels", () => {
  it("counts chats like the design", () => {
    expect(chatsLabel(0)).toBe("No chats yet");
    expect(chatsLabel(1)).toBe("1 chat");
    expect(chatsLabel(3)).toBe("3 chats");
    expect(plural(1, "msg", "msgs")).toBe("1 msg");
    expect(plural(5, "msg", "msgs")).toBe("5 msgs");
  });
  it("prefixes the caller's last line with 'You: '", () => {
    expect(previewText({ preview: "Hi", preview_role: "user" })).toBe("You: Hi");
    expect(previewText({ preview: "Hello", preview_role: "assistant" })).toBe("Hello");
    expect(previewText({ preview: "", preview_role: null })).toBe("No messages");
  });
});

describe("matchesQuery", () => {
  const b = { name: "Corner Bakery", category: "Bakery" };
  it("matches name or category, case-insensitive, trimmed", () => {
    expect(matchesQuery(b, "")).toBe(true);
    expect(matchesQuery(b, "  corner ")).toBe(true);
    expect(matchesQuery({ name: "Lumen", category: "Coffee shop" }, "COFFEE")).toBe(true);
    expect(matchesQuery(b, "dental")).toBe(false);
  });
});

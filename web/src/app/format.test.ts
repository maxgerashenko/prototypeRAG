import { describe, expect, it } from "vitest";
import { chatsLabel, fmtClock, fmtWhen, hostOf, matchesQuery, monogram, plural, previewText } from "./format";

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

describe("hostOf", () => {
  it("prefers the domain, else the website host without www., else 'No website'", () => {
    expect(hostOf({ website: "https://www.abathhouse.com/williamsburg", domain: "abathhouse.com" })).toBe("abathhouse.com");
    expect(hostOf({ website: "https://www.abathhouse.com/williamsburg", domain: null })).toBe("abathhouse.com");
    expect(hostOf({ website: "https://shop.example:8080/x" })).toBe("shop.example:8080");
    expect(hostOf({ website: null, domain: null })).toBe("No website");
    expect(hostOf({ website: "not a url" })).toBe("No website");
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
  it("shows the API's preview, or 'No messages'", () => {
    expect(previewText("You: Hi")).toBe("You: Hi");
    expect(previewText("")).toBe("No messages");
  });
});

describe("matchesQuery", () => {
  const b = { name: "Corner Bakery", website: "https://www.cornerbakery.example/", domain: null };
  it("matches name or host, case-insensitive, trimmed", () => {
    expect(matchesQuery(b, "")).toBe(true);
    expect(matchesQuery(b, "  corner ")).toBe(true);
    expect(matchesQuery({ name: "Bathhouse", website: null, domain: "abathhouse.com" }, "ABATH")).toBe(true);
    expect(matchesQuery(b, "cornerbakery.example")).toBe(true);
    expect(matchesQuery(b, "dental")).toBe(false);
    expect(matchesQuery({ name: "X", website: null }, "no website")).toBe(true); // like main's page
  });
});

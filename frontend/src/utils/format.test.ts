import { describe, expect, it } from "vitest";
import { countLabel, dateLabel, fmtDur, hostOf, initials } from "./format";

describe("initials", () => {
  it("takes the first letter of up to two words", () => {
    expect(initials("Bathhouse Williamsburg")).toBe("BW");
    expect(initials("Corner Bakery")).toBe("CB");
  });
  it("falls back to ? when there is nothing alphanumeric", () => {
    expect(initials("")).toBe("?");
    expect(initials(null)).toBe("?");
    expect(initials("   ")).toBe("?");
  });
  it("ignores extra words beyond two", () => {
    expect(initials("A B C D")).toBe("AB");
  });
});

describe("hostOf", () => {
  it("strips a leading www.", () => {
    expect(hostOf("https://www.abathhouse.com/williamsburg")).toBe("abathhouse.com");
  });
  it("keeps a bare host without www.", () => {
    expect(hostOf("https://corner-bakery.example")).toBe("corner-bakery.example");
  });
  it("returns the no-website copy for null/invalid input", () => {
    expect(hostOf(null)).toBe("No website");
    expect(hostOf(undefined)).toBe("No website");
    expect(hostOf("not a url")).toBe("No website");
  });
});

describe("countLabel", () => {
  it("uses singular/plural/zero copy exactly", () => {
    expect(countLabel(0)).toBe("No chats yet");
    expect(countLabel(1)).toBe("1 chat");
    expect(countLabel(4)).toBe("4 chats");
  });
});

describe("fmtDur", () => {
  it("formats minutes:seconds, zero-padded", () => {
    expect(fmtDur(95)).toBe("1:35");
    expect(fmtDur(45)).toBe("0:45");
    expect(fmtDur(0)).toBe("0:00");
  });
  it("clamps negative/missing durations to 0:00", () => {
    expect(fmtDur(-5)).toBe("0:00");
    expect(fmtDur(null)).toBe("0:00");
    expect(fmtDur(undefined)).toBe("0:00");
  });
});

describe("dateLabel", () => {
  const now = new Date(2026, 9, 4, 15, 30); // 2026-10-04 15:30 local

  it("labels same-day timestamps as Today", () => {
    const sameDay = new Date(2026, 9, 4, 9, 5).toISOString();
    expect(dateLabel(sameDay, now)).toMatch(/^Today · /);
  });
  it("labels the previous day as Yesterday", () => {
    const yesterday = new Date(2026, 9, 3, 9, 5).toISOString();
    expect(dateLabel(yesterday, now)).toMatch(/^Yesterday · /);
  });
  it("labels older dates with month/day", () => {
    const older = new Date(2026, 8, 20, 9, 5).toISOString();
    expect(dateLabel(older, now)).toMatch(/^Sep 20 · /);
  });
});

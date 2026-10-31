import { describe, expect, it } from "vitest";
import type { TwilioNumber } from "../api";
import {
  GREETING_PREFIX,
  assignLabel,
  barWidth,
  cachedLabel,
  canAssign,
  capsLabel,
  filterNumbers,
  greetingView,
  numberSubtitle,
  questionsSummary,
  settingsHint,
} from "./settings";

const num = (over: Partial<TwilioNumber> = {}): TwilioNumber => ({
  number: "+15550001111",
  friendly_name: "(555) 000-1111",
  voice: true,
  sms: true,
  routed: true,
  business_id: null,
  business_name: null,
  ...over,
});

// local time, so the tests pass in any time zone
const NOW = new Date(2026, 9, 31, 15, 0, 0);
const ago = (ms: number) => new Date(NOW.getTime() - ms).toISOString();

describe("greetingView", () => {
  const base = { saved: "Hello", draft: "Hello", cached: true, saving: false, cachedAt: ago(5_000), now: NOW };

  it("cached", () => {
    expect(greetingView(base)).toEqual({
      kind: "cached", chip: "Cached", note: "Cached just now", saveLabel: "Saved", saveDisabled: true, dirty: false, count: 5,
    });
  });
  it("not cached", () => {
    expect(greetingView({ ...base, cached: false })).toEqual({
      kind: "notCached", chip: "Not cached", note: "Generated live on each call", saveLabel: "Cache greeting",
      saveDisabled: false, dirty: false, count: 5,
    });
  });
  it("dirty", () => {
    expect(greetingView({ ...base, draft: "Hello there" })).toEqual({
      kind: "dirty", chip: "Unsaved changes", note: "Save to re-cache", saveLabel: "Save & cache",
      saveDisabled: false, dirty: true, count: 11,
    });
  });
  it("saving beats dirty and cached", () => {
    expect(greetingView({ ...base, draft: "x", saving: true })).toEqual({
      kind: "saving", chip: "Caching…", note: "Generating audio…", saveLabel: "Saving…", saveDisabled: true, dirty: true, count: 1,
    });
    expect(greetingView({ ...base, saving: true }).kind).toBe("saving");
  });
  it("dirty beats cached", () => {
    expect(greetingView({ ...base, draft: "" }).kind).toBe("dirty"); // emptying is allowed: resets to the disclosure alone
    expect(greetingView({ ...base, draft: "" }).saveDisabled).toBe(false);
  });
  it("cached without a time", () => {
    expect(greetingView({ ...base, cachedAt: null }).note).toBe("Cached");
  });
});

describe("cachedLabel", () => {
  it("empty without a valid time", () => {
    expect(cachedLabel(null, NOW)).toBe("");
    expect(cachedLabel("nope", NOW)).toBe("");
  });
  it("just now under a minute", () => {
    expect(cachedLabel(ago(0), NOW)).toBe("just now");
    expect(cachedLabel(ago(59_000), NOW)).toBe("just now");
  });
  it("date and time from a minute", () => {
    expect(cachedLabel(new Date(2026, 9, 28, 16, 12).toISOString(), NOW)).toBe("Oct 28, 4:12 PM");
    expect(cachedLabel(ago(60_000), NOW)).not.toBe("just now");
  });
});

describe("settingsHint", () => {
  it("number and greeting state", () => {
    expect(settingsHint(["+15550001234"], true)).toBe("+1 (555) 000-1234 · Greeting cached");
    expect(settingsHint([], false)).toBe("No number · Greeting not cached");
  });
  it("only the number while the greeting is not loaded", () => {
    expect(settingsHint(["+15550001234"], null)).toBe("+1 (555) 000-1234");
  });
});

describe("capsLabel / numberSubtitle", () => {
  it("caps", () => {
    expect(capsLabel({ voice: true, sms: true })).toBe("Voice & SMS");
    expect(capsLabel({ voice: true, sms: false })).toBe("Voice");
    expect(capsLabel({ voice: false, sms: true })).toBe("SMS");
    expect(capsLabel({ voice: false, sms: false })).toBe("");
  });
  it("friendly name only when it has letters", () => {
    expect(numberSubtitle(num())).toBe("Twilio · Voice & SMS");
    expect(numberSubtitle(num({ friendly_name: "Front desk", sms: false }))).toBe("Front desk · Twilio · Voice");
  });
});

describe("filterNumbers", () => {
  const own = num({ number: "+15550000001", business_id: "b1", business_name: "Mine" });
  const taken = num({ number: "+15550000002", business_id: "b2", business_name: "Other" });
  const free1 = num({ number: "+15550000003", friendly_name: "Sales line" });
  const free2 = num({ number: "+13050000004", friendly_name: "Miami" });
  const list = [taken, free1, own, free2];

  it("own, then free, then taken", () => {
    expect(filterNumbers(list, "", "b1")).toEqual([own, free1, free2, taken]);
  });
  it("digits match the number", () => {
    expect(filterNumbers(list, "305", "b1")).toEqual([free2]);
    expect(filterNumbers(list, "(555) 000-0002", "b1")).toEqual([taken]);
  });
  it("letters match the friendly name", () => {
    expect(filterNumbers(list, "sales", "b1")).toEqual([free1]);
    expect(filterNumbers(list, "zzz", "b1")).toEqual([]);
  });
});

describe("assignLabel / canAssign", () => {
  const free = num();
  const taken = num({ number: "+15550000002", business_id: "b2", business_name: "Other" });
  it("five cases", () => {
    expect(assignLabel(free, null, true)).toBe("Assigning…");
    expect(assignLabel(null, null, false)).toBe("Select a number");
    expect(assignLabel(free, free.number, false)).toBe("Currently assigned");
    expect(assignLabel(taken, free.number, false)).toBe("Move from Other");
    expect(assignLabel(free, null, false)).toBe("Assign +1 (555) 000-1111");
  });
  it("can assign only a different number while idle", () => {
    expect(canAssign(null, null, false)).toBe(false);
    expect(canAssign(free, free.number, false)).toBe(false);
    expect(canAssign(free, null, true)).toBe(false);
    expect(canAssign(taken, free.number, false)).toBe(true);
  });
});

describe("barWidth / questionsSummary / prefix", () => {
  it("clamps to 0-100", () => {
    expect(barWidth(42)).toBe("42%");
    expect(barWidth(-5)).toBe("0%");
    expect(barWidth(250)).toBe("100%");
    expect(barWidth(NaN)).toBe("0%");
  });
  it("summary", () => {
    expect(questionsSummary(12, 5, 30)).toBe("12 questions · 5 calls · 30 days");
  });
  it("prefix is the DEC-53 sentence", () => {
    expect(GREETING_PREFIX).toBe("Hi, I am an AI assistant.");
  });
});

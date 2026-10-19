import { describe, expect, it } from "vitest";
import type { LiveCallInfo, LiveFeedMessage } from "../api";
import { callStatus, elapsed } from "./callState";
import { callerLabel, fmtPhone, latestLive, liveCallsReducer, type PhoneCalls } from "./liveCalls";

const T0 = 1_000_000;
const info = (id = "MZ1", started_at = T0): LiveCallInfo => ({
  id, business_id: "b1", conversation_id: `conv-${id}`, caller: "+15550001111", started_at,
});
const run = (msgs: LiveFeedMessage[], from: PhoneCalls = {}, now = T0) =>
  msgs.reduce((calls, m) => liveCallsReducer(calls, m, now), from);
const event = (e: Record<string, unknown>, t: number, call_id = "MZ1") =>
  ({ type: "call_event", call_id, event: { ...e, t } }) as LiveFeedMessage;

describe("a phone call on the live feed", () => {
  it("goes live when it starts, with the call's own clock", () => {
    const calls = run([{ type: "call_started", call: info() }]);
    const s = calls.MZ1.state;
    expect(s.phase).toBe("live");
    expect(s.conversationId).toBe("conv-MZ1");
    expect(elapsed(s, T0 + 12_500)).toBe(12);
  });

  it("follows the turn: greeting, speaking, caller's words, thinking, answer, idle", () => {
    let calls = run([
      { type: "call_started", call: info() },
      event({ type: "reply", text: "Hi, how can I help?" }, T0 + 500),
      event({ type: "playback", active: true }, T0 + 600),
    ]);
    expect(callStatus(calls.MZ1.state).text).toBe("Assistant is speaking");
    calls = run([event({ type: "playback", active: false }, T0 + 3000), event({ type: "transcript", text: "Open Sunday?" }, T0 + 6000)], calls);
    expect(callStatus(calls.MZ1.state).text).toBe("Thinking…");
    calls = run([
      event({ type: "reply", text: "Yes, 9 to 5." }, T0 + 7000),
      event({ type: "latency", tts_first_audio_ms: 1400 }, T0 + 7100),
      event({ type: "playback", active: true }, T0 + 7100),
      event({ type: "playback", active: false }, T0 + 9000),
    ], calls);
    const s = calls.MZ1.state;
    expect(s.botState).toBeNull();
    expect(s.messages.map((m) => [m.role, m.text, m.at])).toEqual([
      ["bot", "Hi, how can I help?", 0],
      ["user", "Open Sunday?", 6],
      ["bot", "Yes, 9 to 5.", 7],
    ]);
    expect(s.messages[2].latency).toBe("1.4");
  });

  it("marks a barged-in answer as interrupted", () => {
    const calls = run([
      { type: "call_started", call: info() },
      event({ type: "reply", text: "Hi there." }, T0),
      event({ type: "playback", active: true }, T0),
      event({ type: "clear" }, T0 + 1000),
      event({ type: "playback", active: false }, T0 + 1000),
    ]);
    expect(calls.MZ1.state.messages[0].interrupted).toBe(true);
  });

  it("ends on call_ended and keeps the transcript", () => {
    const calls = run([
      { type: "call_started", call: info() },
      event({ type: "transcript", text: "Hello" }, T0 + 2000),
      { type: "call_ended", call_id: "MZ1", t: T0 + 30_000 },
    ]);
    expect(calls.MZ1.state.phase).toBe("ended");
    expect(elapsed(calls.MZ1.state, T0 + 99_000)).toBe(30);
    expect(calls.MZ1.state.messages).toHaveLength(1);
  });

  it("ignores events of calls it doesn't know", () => {
    const calls = run([event({ type: "reply", text: "?" }, T0, "MZ9"), { type: "call_ended", call_id: "MZ9", t: T0 }]);
    expect(calls).toEqual({});
  });
});

describe("snapshot", () => {
  it("replays a call already in progress (page opened mid-call)", () => {
    const calls = run([{
      type: "snapshot",
      calls: [{ ...info(), events: [{ type: "reply", text: "Hi!", t: T0 + 1000 }, { type: "transcript", text: "Prices?", t: T0 + 5000 }] }],
    }]);
    expect(calls.MZ1.state.phase).toBe("live");
    expect(calls.MZ1.state.messages.map((m) => [m.text, m.at])).toEqual([["Hi!", 1], ["Prices?", 5]]);
  });

  it("ends a call that finished while the feed was away", () => {
    const before = run([{ type: "call_started", call: info() }]);
    const after = run([{ type: "snapshot", calls: [] }], before, T0 + 20_000);
    expect(after.MZ1.state.phase).toBe("ended");
    expect(after.MZ1.state.endedAt).toBe(T0 + 20_000);
  });
});

describe("helpers", () => {
  it("latestLive picks the newest call in progress", () => {
    const calls = run([
      { type: "call_started", call: info("A", T0) },
      { type: "call_started", call: info("B", T0 + 1000) },
      { type: "call_ended", call_id: "B", t: T0 + 2000 },
    ]);
    expect(latestLive(calls)?.info.id).toBe("A");
    expect(latestLive({})).toBeUndefined();
  });

  it("fmtPhone", () => {
    expect(fmtPhone("+12223334512")).toBe("+1 222 333 45 12");
    expect(fmtPhone("+37126123456")).toBe("+371 261 234 56");
    expect(fmtPhone("+447911123456")).toBe("+44 791 112 34 56");
    expect(fmtPhone("+4930123456")).toBe("+49 301 234 56");
    expect(fmtPhone("+1 (222) 333-4512")).toBe("+1 222 333 45 12");
    expect(fmtPhone("anonymous")).toBe("anonymous");
  });

  it("callerLabel", () => {
    expect(callerLabel("+15550001111")).toBe("+1 555 000 11 11");
    expect(callerLabel("client:browser-test")).toBe("Browser test call");
    expect(callerLabel(null)).toBe("Unknown caller");
  });
});

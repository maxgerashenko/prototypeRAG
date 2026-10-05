import { describe, expect, it } from "vitest";
import type { VoiceEvent } from "../api";
import {
  NO_SPEECH_HINT,
  callReducer,
  callerSpoke,
  callStatus,
  elapsed,
  initialCallState,
  spokenCount,
  type CallAction,
  type CallState,
} from "./callState";

const T0 = 1_000_000;
const ev = (e: VoiceEvent, now = T0): CallAction => ({ type: "event", ev: e, now });
const run = (actions: CallAction[], from: CallState = initialCallState) => actions.reduce(callReducer, from);
const live = (now = T0) => run([ev({ type: "conversation_id", id: "c1" }, now)]);

describe("connecting -> live", () => {
  it("goes live on conversation_id and starts the clock", () => {
    const s = live();
    expect(s.phase).toBe("live");
    expect(s.conversationId).toBe("c1");
    expect(elapsed(s, T0 + 84_500)).toBe(84);
  });
  it("keeps a greeting that arrives before conversation_id at 0:00", () => {
    const s = run([ev({ type: "reply", text: "Hi!" }), ev({ type: "conversation_id", id: "c1" }, T0 + 5000)]);
    expect(s.messages).toEqual([{ id: 0, role: "bot", text: "Hi!", at: 0 }]);
    expect(s.phase).toBe("live");
  });
  it("ignores a second conversation_id", () => {
    const s = run([ev({ type: "conversation_id", id: "c2" }, T0 + 9000)], live());
    expect(s.conversationId).toBe("c1");
    expect(s.startedAt).toBe(T0);
  });
  it("ignores push-to-talk until live", () => {
    expect(callReducer(initialCallState, { type: "pttDown" })).toBe(initialCallState);
  });
});

describe("a hold-to-talk turn", () => {
  it("listening -> thinking -> speaking -> idle", () => {
    let s = live();
    s = callReducer(s, { type: "pttDown" });
    expect(callStatus(s)).toMatchObject({ text: "Listening to you…", tone: "call", orbActive: true });
    s = callReducer(s, { type: "pttUp" });
    expect(s.botState).toBe("thinking");
    expect(callStatus(s).text).toBe("Thinking…");

    s = callReducer(s, ev({ type: "transcript", text: "Open Sunday?" }, T0 + 41_000));
    expect(s.messages.at(-1)).toMatchObject({ role: "user", text: "Open Sunday?", at: 41 });
    expect(s.botState).toBe("thinking");

    s = callReducer(s, ev({ type: "reply", text: "Yes, 8 to 2." }, T0 + 44_000));
    s = callReducer(s, { type: "playback", active: true });
    expect(callStatus(s)).toMatchObject({ text: "Assistant is speaking", tone: "bot", orbActive: true });

    // a gap between sentences doesn't end "speaking" while the turn is still running
    s = callReducer(s, { type: "playback", active: false });
    expect(s.botState).toBe("speaking");
    s = callReducer(s, ev({ type: "reply", text: "Anything else?" }, T0 + 46_000));
    s = callReducer(s, { type: "playback", active: true });
    s = callReducer(s, ev({ type: "latency", stt_ms: 120 }));
    expect(s.botState).toBe("speaking"); // audio still playing
    s = callReducer(s, { type: "playback", active: false });
    expect(s.botState).toBe(null);
    expect(callStatus(s).text).toBe("Hold the button to talk");

    // sentences of one reply share a bubble, stamped with the first sentence's time
    expect(s.messages.at(-1)).toMatchObject({ role: "bot", text: "Yes, 8 to 2. Anything else?", at: 44 });
    expect(spokenCount(s)).toBe(2);
  });

  it("latency after playback already drained ends speaking right away", () => {
    let s = run([{ type: "pttDown" }, { type: "pttUp" }, ev({ type: "transcript", text: "q" }), ev({ type: "reply", text: "a" }),
      { type: "playback", active: true }, { type: "playback", active: false }], live());
    expect(s.botState).toBe("speaking");
    s = callReducer(s, ev({ type: "latency", total_ms: 900 }));
    expect(s.botState).toBe(null);
  });

  it("the greeting stops 'speaking' when its audio ends", () => {
    let s = run([ev({ type: "reply", text: "Hi!" }), { type: "playback", active: true }], live());
    expect(s.botState).toBe("speaking");
    s = callReducer(s, { type: "playback", active: false });
    expect(s.botState).toBe(null);
  });

  it("a new transcript starts a new bot bubble", () => {
    const s = run([ev({ type: "reply", text: "Hi!" }), ev({ type: "transcript", text: "q" }), ev({ type: "reply", text: "a" })], live());
    expect(s.messages.map((m) => [m.role, m.text])).toEqual([["bot", "Hi!"], ["user", "q"], ["bot", "a"]]);
  });

  it("pressing again while the bot speaks interrupts it (barge-in)", () => {
    let s = run([ev({ type: "reply", text: "Long answer" }), { type: "playback", active: true }], live());
    s = callReducer(s, { type: "pttDown" });
    expect(s.talking).toBe(true);
    expect(s.botState).toBe(null);
    // replies arriving while held don't flip the status away from listening
    s = callReducer(s, ev({ type: "reply", text: "more" }));
    expect(callStatus(s).text).toBe("Listening to you…");
  });

  it("a double press or a release without press is ignored", () => {
    const s = callReducer(live(), { type: "pttDown" });
    expect(callReducer(s, { type: "pttDown" })).toBe(s);
    expect(callReducer(live(), { type: "pttUp" }).botState).toBe(null);
  });
});

describe("no speech heard", () => {
  it("leaves 'thinking' with a hint when no transcript comes", () => {
    let s = run([{ type: "pttDown" }, { type: "pttUp" }, { type: "noSpeech" }], live());
    expect(s.botState).toBe(null);
    expect(callStatus(s).text).toBe(NO_SPEECH_HINT);
    s = callReducer(s, { type: "pttDown" });
    expect(s.hint).toBe(null);
  });
  it("does nothing once the transcript arrived", () => {
    const s = run([{ type: "pttDown" }, { type: "pttUp" }, ev({ type: "transcript", text: "q" }), { type: "noSpeech" }], live());
    expect(s.botState).toBe("thinking");
    expect(s.hint).toBe(null);
  });
});

describe("server push-to-talk events (DEC-42)", () => {
  it("no_speech from the server leaves 'thinking' with main's hint", () => {
    const s = run([{ type: "pttDown" }, { type: "pttUp" }, ev({ type: "no_speech" })], live());
    expect(s.botState).toBe(null);
    expect(s.pendingTurn).toBe(false);
    expect(callStatus(s).text).toBe("Didn't catch that — hold and try again");
    expect(NO_SPEECH_HINT).toBe("Didn't catch that — hold and try again");
  });
  it("no_speech while the caller holds again doesn't interrupt the new turn", () => {
    const s = run([{ type: "pttDown" }, { type: "pttUp" }, { type: "pttDown" }, ev({ type: "no_speech" })], live());
    expect(callStatus(s).text).toBe("Listening to you…");
  });
  it("latency puts the time to first audio on the answer, all timings in its title", () => {
    const s = run([ev({ type: "transcript", text: "q" }), ev({ type: "reply", text: "a" }),
      ev({ type: "latency", stt_ms: 80, tts_first_audio_ms: 1400, total_ms: 1600 })], live());
    expect(s.messages.at(-1)).toMatchObject({ role: "bot", latency: "1.4", latencyTitle: "stt_ms: 80\ntts_first_audio_ms: 1400\ntotal_ms: 1600" });
  });
  it("latency without a first-audio time only sets the title; no answer -> nothing to mark", () => {
    const s = run([ev({ type: "reply", text: "a" }), ev({ type: "latency", stt_ms: 80 })], live());
    expect(s.messages[0].latency).toBeUndefined();
    expect(s.messages[0].latencyTitle).toBe("stt_ms: 80");
    expect(run([ev({ type: "latency", stt_ms: 80 })], live()).messages).toEqual([]);
  });
  it("clear marks the answer being spoken as interrupted; the next sentence starts a new bubble", () => {
    let s = run([ev({ type: "reply", text: "Starting response" }), ev({ type: "clear" })], live());
    expect(s.messages.at(-1)).toMatchObject({ text: "Starting response", interrupted: true });
    s = callReducer(s, ev({ type: "reply", text: "Next" }));
    expect(s.messages.map((m) => m.text)).toEqual(["Starting response", "Next"]);
    // clear with no answer in progress changes nothing
    const idle = run([ev({ type: "transcript", text: "q" })], live());
    expect(callReducer(idle, ev({ type: "clear" }))).toBe(idle);
  });
  it("callerSpoke: only calls with a user message can be read later", () => {
    expect(callerSpoke(run([ev({ type: "reply", text: "Hi" })], live()))).toBe(false);
    expect(callerSpoke(run([ev({ type: "transcript", text: "q" })], live()))).toBe(true);
  });
  it("a server close records its code (4404 = unknown business)", () => {
    const s = callReducer(live(), { type: "closed", code: 4404, reason: "unknown business", byServer: true, now: T0 });
    expect(s.endCode).toBe(4404);
    expect(callReducer(live(), { type: "closed", code: 1005, reason: "", byServer: false, now: T0 }).endCode).toBe(null);
  });
});

describe("errors and endings", () => {
  it("shows server errors as a notice and leaves 'thinking'", () => {
    const s = run([{ type: "pttDown" }, { type: "pttUp" }, ev({ type: "error", message: "TTS down" })], live());
    expect(s.messages.at(-1)).toMatchObject({ role: "system", text: "Error: TTS down" });
    expect(s.botState).toBe(null);
    expect(spokenCount(s)).toBe(0);
  });
  it("ignores unknown events", () => {
    const s = live();
    expect(callReducer(s, ev({ type: "something_new" } as unknown as VoiceEvent))).toBe(s);
  });
  it("hang-up freezes the clock", () => {
    const s = run([{ type: "pttDown" }, { type: "hangup", now: T0 + 30_000 }], live());
    expect(s.phase).toBe("ended");
    expect(s.talking).toBe(false);
    expect(elapsed(s, T0 + 99_000)).toBe(30);
    expect(s.endReason).toBe(null);
    expect(callReducer(s, { type: "hangup", now: T0 + 50_000 }).endedAt).toBe(T0 + 30_000);
  });
  it("a server close records the reason; a close we caused doesn't", () => {
    const byServer = callReducer(live(), { type: "closed", code: 4404, reason: "unknown business", byServer: true, now: T0 });
    expect(byServer.endReason).toBe("unknown business");
    const noReason = callReducer(live(), { type: "closed", code: 1011, reason: "", byServer: true, now: T0 });
    expect(noReason.endReason).toBe("Connection closed (code 1011)");
    const ours = callReducer(live(), { type: "closed", code: 1005, reason: "", byServer: false, now: T0 });
    expect(ours.endReason).toBe(null);
    expect(callReducer(ours, { type: "closed", code: 1, reason: "x", byServer: true, now: T0 })).toBe(ours);
  });
  it("reset starts over", () => {
    expect(callReducer(live(), { type: "reset" })).toBe(initialCallState);
  });
  it("the clock is 0 before going live", () => {
    expect(elapsed(initialCallState, T0)).toBe(0);
  });
});

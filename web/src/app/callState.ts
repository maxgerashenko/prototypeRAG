// Live-call state for the voice app: a pure reducer over server events and hold-to-talk
// input (unit-tested in callState.test.ts). useVoiceCall.ts wires it to VoiceCall.
//
// Turn flow with hold-to-talk (DEC-42, server push-to-talk: ptt_start/ptt_end): press ->
// "listening" (mic transmits, bot audio stops) -> release -> "thinking" until the server's
// transcript and first reply sentence -> "speaking" while reply audio plays -> idle once the
// turn is done (latency event) and the playback queue is empty. A turn with nothing said
// gets `no_speech` from the server; if even that never comes, the hook fires `noSpeech`
// after NO_SPEECH_MS so the call never hangs in "thinking".

import type { VoiceEvent } from "../api";

export const NO_SPEECH_MS = 6000;

export type BotState = null | "thinking" | "speaking";

export interface CallMessage {
  id: number;
  role: "bot" | "user" | "system";
  text: string;
  at: number; // seconds since the call went live
  latency?: string; // bot: seconds to the first audio of this answer, e.g. "1.4"
  latencyTitle?: string; // bot: every timing of the turn, one "name: value" per line
  interrupted?: boolean; // bot: cut off by the caller (barge-in)
}

export interface CallState {
  phase: "connecting" | "live" | "ended";
  conversationId: string | null;
  startedAt: number | null; // ms epoch when the server confirmed the call
  endedAt: number | null;
  talking: boolean;
  botState: BotState;
  pendingTurn: boolean; // released, turn not finished yet
  heardUser: boolean; // the pending turn got a transcript
  mergeReply: boolean; // next reply sentence joins the last bot bubble
  playing: boolean;
  messages: CallMessage[];
  hint: string | null;
  endReason: string | null; // set when the server closed the call
  endCode: number | null; // the server's close code (4404 = unknown business)
}

export type CallAction =
  | { type: "event"; ev: VoiceEvent; now: number }
  | { type: "pttDown" }
  | { type: "pttUp" }
  | { type: "playback"; active: boolean }
  | { type: "noSpeech" }
  | { type: "closed"; code: number; reason: string; byServer: boolean; now: number }
  | { type: "hangup"; now: number }
  | { type: "reset" };

export const initialCallState: CallState = {
  phase: "connecting",
  conversationId: null,
  startedAt: null,
  endedAt: null,
  talking: false,
  botState: null,
  pendingTurn: false,
  heardUser: false,
  mergeReply: false,
  playing: false,
  messages: [],
  hint: null,
  endReason: null,
  endCode: null,
};

export const NO_SPEECH_HINT = "Didn't catch that — hold and try again";

function at(s: CallState, now: number): number {
  return s.startedAt == null ? 0 : Math.max(0, Math.floor((now - s.startedAt) / 1000));
}

function push(s: CallState, role: CallMessage["role"], text: string, now: number): CallMessage[] {
  const id = s.messages.length ? s.messages[s.messages.length - 1].id + 1 : 0;
  return [...s.messages, { id, role, text, at: at(s, now) }];
}

function onEvent(s: CallState, ev: VoiceEvent, now: number): CallState {
  switch (ev.type) {
    case "conversation_id":
      return s.phase === "connecting" ? { ...s, phase: "live", conversationId: ev.id, startedAt: now } : s;
    case "transcript":
      return {
        ...s,
        messages: push(s, "user", ev.text, now),
        mergeReply: false,
        heardUser: true,
        botState: s.talking ? s.botState : "thinking",
      };
    case "reply": {
      const last = s.messages[s.messages.length - 1];
      const messages =
        last && last.role === "bot" && s.mergeReply
          ? [...s.messages.slice(0, -1), { ...last, text: `${last.text} ${ev.text}` }]
          : push(s, "bot", ev.text, now);
      return { ...s, messages, mergeReply: true, botState: s.talking ? s.botState : "speaking" };
    }
    case "latency": {
      // the answer being built, else the last one (main's applyLatency)
      let idx = -1;
      for (let i = s.messages.length - 1; i >= 0; i--) if (s.messages[i].role === "bot") { idx = i; break; }
      const { type: _type, ...timings } = ev;
      const messages = [...s.messages];
      if (idx >= 0) {
        const ms = timings.tts_first_audio_ms;
        messages[idx] = {
          ...messages[idx],
          ...(typeof ms === "number" ? { latency: (ms / 1000).toFixed(1) } : {}),
          latencyTitle: Object.entries(timings).map(([k, v]) => `${k}: ${v}`).join("\n"),
        };
      }
      return {
        ...s,
        messages,
        pendingTurn: false,
        botState: s.talking || (s.botState === "speaking" && s.playing) ? s.botState : null,
      };
    }
    case "no_speech":
      return { ...s, pendingTurn: false, botState: s.talking ? s.botState : null, hint: s.talking ? s.hint : NO_SPEECH_HINT };
    case "error":
      return {
        ...s,
        messages: push(s, "system", `Error: ${ev.message}`, now),
        pendingTurn: false,
        botState: s.talking ? s.botState : null,
      };
    case "clear": {
      // barge-in: the answer being spoken is cut off (playback stop arrives as a `playback` action)
      const last = s.messages[s.messages.length - 1];
      if (!last || last.role !== "bot" || !s.mergeReply) return s;
      return { ...s, mergeReply: false, messages: [...s.messages.slice(0, -1), { ...last, interrupted: true }] };
    }
    default:
      return s;
  }
}

export function callReducer(s: CallState, a: CallAction): CallState {
  switch (a.type) {
    case "event":
      return onEvent(s, a.ev, a.now);
    case "pttDown":
      if (s.phase !== "live" || s.talking) return s;
      return { ...s, talking: true, botState: null, pendingTurn: false, heardUser: false, hint: null };
    case "pttUp":
      if (!s.talking) return s;
      return { ...s, talking: false, botState: "thinking", pendingTurn: true, heardUser: false };
    case "playback":
      if (a.active) return { ...s, playing: true, botState: s.talking ? s.botState : "speaking" };
      return {
        ...s,
        playing: false,
        botState: s.botState === "speaking" && !s.pendingTurn ? null : s.botState,
      };
    case "noSpeech":
      if (!s.pendingTurn || s.heardUser || s.talking) return s;
      return { ...s, pendingTurn: false, botState: null, hint: NO_SPEECH_HINT };
    case "closed":
      if (s.phase === "ended") return s;
      return {
        ...s,
        phase: "ended",
        endedAt: a.now,
        talking: false,
        botState: null,
        pendingTurn: false,
        endReason: a.byServer ? a.reason || `Connection closed (code ${a.code})` : null,
        endCode: a.byServer ? a.code : null,
      };
    case "reset":
      return initialCallState;
    case "hangup":
      if (s.phase === "ended") return s;
      return { ...s, phase: "ended", endedAt: a.now, talking: false, botState: null, pendingTurn: false };
  }
}

/** Seconds on the call clock. */
export function elapsed(s: CallState, now: number): number {
  if (s.startedAt == null) return 0;
  return Math.max(0, Math.floor(((s.endedAt ?? now) - s.startedAt) / 1000));
}

/** Messages the caller sees counted on the "Call ended" screen (system notices excluded). */
export function spokenCount(s: CallState): number {
  return s.messages.filter((m) => m.role !== "system").length;
}

/** A call is saved for reading only if the caller said something (the API hides silent calls). */
export function callerSpoke(s: CallState): boolean {
  return s.messages.some((m) => m.role === "user");
}

export interface CallStatus {
  text: string;
  tone: "idle" | "call" | "bot";
  orbActive: boolean;
  barsRunning: boolean;
  barSpeed: string;
}

/** Orb + status line under it, as in the design's renderVals(). */
export function callStatus(s: CallState): CallStatus {
  if (s.talking) return { text: "Listening to you…", tone: "call", orbActive: true, barsRunning: true, barSpeed: ".7s" };
  if (s.botState === "thinking")
    return { text: "Thinking…", tone: "bot", orbActive: false, barsRunning: true, barSpeed: "1.8s" };
  if (s.botState === "speaking")
    return { text: "Assistant is speaking", tone: "bot", orbActive: true, barsRunning: true, barSpeed: ".85s" };
  return { text: s.hint ?? "Hold the button to talk", tone: "idle", orbActive: false, barsRunning: false, barSpeed: "1s" };
}

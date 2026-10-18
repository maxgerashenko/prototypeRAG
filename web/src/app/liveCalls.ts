// Phone calls the system is taking right now (DEC-44), from GET /voice/feed: a pure
// reducer (unit-tested in liveCalls.test.ts) that turns feed messages into one CallState per
// call with the same callReducer the app's own calls use, so a phone call shows on the same
// call screen. useLiveCalls.ts wires it to an EventSource.

import type { LiveCallInfo, LiveEvent, LiveFeedMessage } from "../api";
import { callReducer, initialCallState, type CallState } from "./callState";

export interface PhoneCall {
  info: LiveCallInfo;
  state: CallState;
}

/** Calls by id, in the order they started; ended calls stay (phase "ended") so their
 * summary can still be shown. */
export type PhoneCalls = Record<string, PhoneCall>;

function applyEvent(state: CallState, ev: LiveEvent): CallState {
  if (ev.type === "playback") return callReducer(state, { type: "playback", active: ev.active });
  const { t, ...rest } = ev;
  return callReducer(state, { type: "event", ev: rest as Exclude<LiveEvent, { type: "playback" }>, now: t });
}

function started(info: LiveCallInfo): PhoneCall {
  // the server sends no conversation_id event on the phone line; this one makes the call
  // live with the clock started when the call did
  const state = callReducer(initialCallState, {
    type: "event",
    ev: { type: "conversation_id", id: info.conversation_id ?? "" },
    now: info.started_at,
  });
  return { info, state: { ...state, conversationId: info.conversation_id } };
}

/** `now` ends calls that vanished while the feed was disconnected. */
export function liveCallsReducer(calls: PhoneCalls, msg: LiveFeedMessage, now: number): PhoneCalls {
  switch (msg.type) {
    case "snapshot": {
      const next: PhoneCalls = {};
      for (const [id, c] of Object.entries(calls)) {
        // a call we showed as live but the server no longer has: it ended meanwhile
        const gone = c.state.phase !== "ended" && !msg.calls.some((s) => s.id === id);
        next[id] = gone ? { ...c, state: callReducer(c.state, { type: "hangup", now }) } : c;
      }
      for (const { events, ...info } of msg.calls) {
        next[info.id] = { info, state: events.reduce(applyEvent, started(info).state) };
      }
      return next;
    }
    case "call_started":
      return { ...calls, [msg.call.id]: started(msg.call) };
    case "call_event": {
      const c = calls[msg.call_id];
      if (!c) return calls;
      return { ...calls, [msg.call_id]: { ...c, state: applyEvent(c.state, msg.event) } };
    }
    case "call_ended": {
      const c = calls[msg.call_id];
      if (!c) return calls;
      return { ...calls, [msg.call_id]: { ...c, state: callReducer(c.state, { type: "hangup", now: msg.t }) } };
    }
  }
}

/** The newest call still in progress, if any. */
export function latestLive(calls: PhoneCalls): PhoneCall | undefined {
  return Object.values(calls)
    .filter((c) => c.state.phase === "live")
    .sort((a, b) => b.info.started_at - a.info.started_at)[0];
}

// country codes are 1, 2 or 3 digits (ITU E.164); the 1- and 2-digit ones are listed,
// everything else is 3 digits (e.g. +371 Latvia)
const CC1 = new Set(["1", "7"]);
const CC2 = new Set(("20 27 30 31 32 33 34 36 39 40 41 43 44 45 46 47 48 49 51 52 53 54 55 56 57 58 " +
  "60 61 62 63 64 65 66 81 82 84 86 90 91 92 93 94 95 98").split(" "));

/** E.164 for reading: "+12223334512" -> "+1 222 333 45 12", "+37126123456" -> "+371 261 234 56".
 * After the country code: groups of 3, 3, then 2s (a last single digit joins the group
 * before). Anything that isn't "+" and digits is returned as is. */
export function fmtPhone(number: string): string {
  const m = /^\+(\d{4,15})$/.exec(number.replace(/[\s().-]/g, ""));
  if (!m) return number;
  const d = m[1];
  const ccLen = CC1.has(d[0]) ? 1 : CC2.has(d.slice(0, 2)) ? 2 : 3;
  let rest = d.slice(ccLen);
  const groups: string[] = [];
  for (const size of [3, 3]) {
    if (rest.length > size + 1) {
      groups.push(rest.slice(0, size));
      rest = rest.slice(size);
    }
  }
  while (rest.length > 3) {
    groups.push(rest.slice(0, 2));
    rest = rest.slice(2);
  }
  if (rest) groups.push(rest);
  return `+${d.slice(0, ccLen)} ${groups.join(" ")}`;
}

/** A number reads as "+1 222 333 45 12"; a Voice SDK test call ("client:browser-test")
 * as a browser test call. */
export function callerLabel(caller: string | null): string {
  if (!caller) return "Unknown caller";
  if (caller.startsWith("client:")) return "Browser test call";
  return fmtPhone(caller);
}

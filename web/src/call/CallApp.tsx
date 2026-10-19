// /web/call.html — demo mode B (plan/03-voice-channel.md, DEC-34): a real Twilio call from the
// browser through the Voice SDK, no phone number. Twilio calls our TwiML App's voice URL
// (POST /twilio/voice, via ngrok) with `business_id` as a custom parameter, then streams the
// call's 8 kHz μ-law audio to /voice/ws — the same path a phone call takes. Unlike mode C the
// page sees no transcripts: the audio goes browser → Twilio → our server and back. Read them
// in the server log or in the voice app's conversation list afterwards.
//
// The access token comes from GET /twilio/token, which only answers on localhost — open this
// page at http://localhost:8000/web/call.html, not through the ngrok URL.

import { useEffect, useRef, useState } from "react";
import { Call, Device } from "@twilio/voice-sdk";
import { fetchToken, type Business } from "../api";

interface Line {
  id: number;
  text: string;
}

type Phase = "idle" | "connecting" | "live";

export function CallApp() {
  const [businesses, setBusinesses] = useState<Business[]>([]);
  const [businessId, setBusinessId] = useState("");
  const [phase, setPhase] = useState<Phase>("idle");
  const [lines, setLines] = useState<Line[]>([]);
  const device = useRef<Device | null>(null);
  const call = useRef<Call | null>(null);
  const nextId = useRef(0);
  const attempt = useRef(0); // bumped by "Hang up" while connecting, so a late connect is dropped

  function log(text: string) {
    const id = nextId.current++;
    setLines((prev) => [...prev, { id, text }]);
  }

  useEffect(() => {
    fetch("/businesses")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((list: Business[]) => {
        setBusinesses(list);
        if (list.length > 0) setBusinessId((cur) => cur || list[0].id);
      })
      .catch((err: unknown) => log("Could not load businesses: " + String(err)));
    return () => {
      call.current?.disconnect();
      device.current?.destroy();
    };
  }, []);

  function ended(text: string) {
    log(text);
    call.current = null;
    setPhase("idle");
  }

  async function onCall() {
    if (phase !== "idle") {
      attempt.current++;
      if (call.current) call.current.disconnect(); // "disconnect" event → ended()
      else ended("Cancelled.");
      return;
    }
    const mine = ++attempt.current;
    const bid = businessId.trim();
    if (!bid) return;
    setPhase("connecting");
    try {
      if (!device.current) {
        const { token } = await fetchToken();
        device.current = new Device(token, { logLevel: "warn", codecPreferences: [Call.Codec.Opus, Call.Codec.PCMU] });
        device.current.on("error", (err: { message?: string }) => log("Device error: " + (err.message ?? String(err))));
      }
      log(`Calling business ${bid} …`);
      const c = await device.current.connect({ params: { business_id: bid } });
      if (attempt.current !== mine) {
        c.disconnect();
        return;
      }
      call.current = c;
      c.on("accept", () => {
        log("Connected — speak after the greeting.");
        setPhase("live");
      });
      c.on("disconnect", () => ended("Call ended."));
      c.on("cancel", () => ended("Call cancelled."));
      c.on("reject", () => ended("Call rejected."));
      c.on("error", (err: { message?: string }) => log("Call error: " + (err.message ?? String(err))));
    } catch (err: unknown) {
      if (attempt.current !== mine) return;
      // a stale/invalid token: drop the device so the next click fetches a new one
      device.current?.destroy();
      device.current = null;
      ended("Error: " + (err instanceof Error ? err.message : String(err)));
    }
  }

  return (
    <div className="container">
      <label htmlFor="business">Business:</label>
      <select id="business" value={businessId} onChange={(e) => setBusinessId(e.target.value)} disabled={phase !== "idle"}>
        {businesses.map((b) => (
          <option key={b.id} value={b.id}>
            {b.name}
            {b.domain ? ` (${b.domain})` : ""}
          </option>
        ))}
      </select>
      <p className="hint">
        Mode B: browser → Twilio Voice SDK → TwiML App → POST /twilio/voice → Media Stream /voice/ws (8 kHz μ-law).
        Needs the TWILIO_* settings in .env and the TwiML App's voice URL pointing at the ngrok URL. Billed per minute.
      </p>
      <button id="talk" className={phase === "idle" ? undefined : "live"} onClick={onCall}>
        {phase === "idle" ? "Call" : "Hang up"}
      </button>
      <span id="status">{phase === "connecting" ? "connecting…" : phase === "live" ? "in call" : ""}</span>
      <div id="log">
        {lines.map((l) => (
          <div key={l.id} className="line meta">
            {l.text}
          </div>
        ))}
      </div>
    </div>
  );
}

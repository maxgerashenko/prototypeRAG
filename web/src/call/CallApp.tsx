// /web/call.html — a phone call through Twilio from the browser (demo mode B, DEC-34):
// Voice SDK -> our TwiML App -> POST /twilio/voice -> <Stream> to /voice/ws, the same path a
// dialled number takes (app/voice/twilio_routes.py, app/voice/ws.py). Costs Twilio minutes,
// no phone number. Needs TWILIO_* in .env and the TwiML App's voice URL pointing at
// https://<ngrok>/twilio/voice; for $0 tests use the voice app (/web/, mode C) instead.

import { useEffect, useRef, useState } from "react";
import { Call, Device } from "@twilio/voice-sdk";
import { listBusinesses, type Business } from "../api";

type Status = "idle" | "connecting" | "ringing" | "in call" | "ended";

interface LogLine {
  id: number;
  text: string;
  error?: boolean;
}

async function fetchToken(): Promise<string> {
  const res = await fetch("/twilio/token");
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail ?? `HTTP ${res.status}`);
  return body.token as string;
}

export function CallApp() {
  const [businesses, setBusinesses] = useState<Business[]>([]);
  const [businessId, setBusinessId] = useState("");
  const [status, setStatus] = useState<Status>("idle");
  const [muted, setMuted] = useState(false);
  const [log, setLog] = useState<LogLine[]>([]);
  const device = useRef<Device | null>(null);
  const call = useRef<Call | null>(null);
  const nextId = useRef(0);

  const write = (text: string, error = false) => {
    const line = { id: nextId.current++, text: `${new Date().toLocaleTimeString()} ${text}`, error };
    setLog((prev) => [...prev, line]);
  };

  useEffect(() => {
    listBusinesses()
      .then((list) => {
        setBusinesses(list);
        if (list.length > 0) setBusinessId((id) => id || list[0].id);
      })
      .catch((e: Error) => write(`could not load businesses: ${e.message}`, true));
    return () => device.current?.destroy();
  }, []);

  async function getDevice(): Promise<Device> {
    if (device.current) return device.current;
    const d = new Device(await fetchToken(), {
      codecPreferences: [Call.Codec.Opus, Call.Codec.PCMU],
      closeProtection: true, // a call in progress asks before the tab closes
    });
    d.on("error", (e: { message: string }) => write(`device error: ${e.message}`, true));
    d.on("tokenWillExpire", async () => {
      try {
        d.updateToken(await fetchToken());
      } catch (e) {
        write(`token refresh failed: ${(e as Error).message}`, true);
      }
    });
    device.current = d;
    return d;
  }

  async function startCall() {
    if (!businessId) return;
    setStatus("connecting");
    setMuted(false);
    try {
      const d = await getDevice();
      const c = await d.connect({ params: { business_id: businessId } });
      call.current = c;
      write(`calling ${businesses.find((b) => b.id === businessId)?.name ?? businessId}`);
      c.on("ringing", () => setStatus("ringing"));
      c.on("accept", () => {
        setStatus("in call");
        write(`connected (${c.parameters.CallSid ?? "no CallSid"})`);
      });
      c.on("disconnect", () => {
        setStatus("ended");
        call.current = null;
        write("call ended");
      });
      c.on("cancel", () => {
        setStatus("ended");
        call.current = null;
      });
      c.on("error", (e: { message: string }) => write(`call error: ${e.message}`, true));
      c.on("warning", (name: string) => write(`network warning: ${name}`));
    } catch (e) {
      setStatus("idle");
      write((e as Error).message, true);
    }
  }

  function toggleMute() {
    const c = call.current;
    if (!c) return;
    c.mute(!muted);
    setMuted(!muted);
  }

  const active = status === "connecting" || status === "ringing" || status === "in call";

  return (
    <div className="container">
      <h1>Twilio call test</h1>
      <p className="hint">Mode B: a real Twilio call from this browser (costs call minutes). For $0 tests use the voice app.</p>
      <label htmlFor="business">Business</label>
      <select id="business" value={businessId} onChange={(e) => setBusinessId(e.target.value)} disabled={active}>
        {businesses.map((b) => (
          <option key={b.id} value={b.id}>{b.name}</option>
        ))}
      </select>
      <div className="controls">
        <button id="call" onClick={startCall} disabled={active || !businessId}>Call</button>
        <button id="mute" onClick={toggleMute} disabled={status !== "in call"}>{muted ? "Unmute" : "Mute"}</button>
        <button id="hangup" onClick={() => call.current?.disconnect()} disabled={!active}>Hang up</button>
        <span id="status">{status}</span>
      </div>
      <div id="log">
        {log.map((l) => (
          <div key={l.id} className={l.error ? "error" : undefined}>{l.text}</div>
        ))}
      </div>
    </div>
  );
}

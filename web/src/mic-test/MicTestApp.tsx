import { useEffect, useRef, useState } from "react";
import { VoiceCall, type LineKind } from "../voice/voiceCall";

interface Line {
  id: number;
  kind: LineKind;
  text: string;
}

export function MicTestApp() {
  const [businessId, setBusinessId] = useState("");
  const [lines, setLines] = useState<Line[]>([]);
  const [live, setLive] = useState(false);
  const call = useRef<VoiceCall | null>(null);
  const nextId = useRef(0);
  const logEl = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = logEl.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [lines]);

  // hang up if the page unmounts mid-call
  useEffect(() => () => call.current?.stop(), []);

  function addLine(kind: LineKind, text: string) {
    const id = nextId.current++;
    setLines((prev) => [...prev, { id, kind, text }]);
  }

  function onTalk() {
    if (call.current) {
      call.current.stop();
      return;
    }
    const bid = businessId.trim();
    if (!bid) return;
    const c = new VoiceCall({
      onLine: addLine,
      // stop() runs again from the socket's late onclose; by then a newer call may be live
      onEnded: () => {
        if (call.current !== c) return;
        call.current = null;
        setLive(false);
      },
    });
    call.current = c;
    // like the legacy page, the button turns to "Hang up" only once mic + socket are up;
    // a click while starting hangs up instead of starting a second call
    c.start(bid)
      .then(() => {
        if (call.current === c) setLive(true);
      })
      .catch((err: unknown) => {
        addLine("meta", "Error: " + (err instanceof Error ? err.message : String(err)));
        c.stop();
      });
  }

  return (
    <div className="container">
      <label htmlFor="business_id">Business ID (UUID):</label>
      <input
        type="text"
        id="business_id"
        placeholder="Paste UUID here"
        value={businessId}
        onChange={(e) => setBusinessId(e.target.value)}
      />
      <p className="hint">
        Mode C: mic → WebSocket /voice/browser (PCM16, 16 kHz) → VAD → Google STT → LLM + tools → Google TTS.
        Use headphones — speaker echo reaching the mic counts as barge-in. Chrome or Safari.
      </p>
      <button id="talk" className={live ? "live" : undefined} onClick={onTalk}>
        {live ? "Hang up" : "Talk"}
      </button>
      <span id="status">{live ? "listening…" : ""}</span>
      <div id="log" ref={logEl}>
        {lines.map((l) => (
          <div key={l.id} className={`line ${l.kind}`}>
            {l.text}
          </div>
        ))}
      </div>
    </div>
  );
}

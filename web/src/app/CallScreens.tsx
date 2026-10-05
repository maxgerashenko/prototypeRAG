import { useEffect, useState, type CSSProperties, type KeyboardEvent, type PointerEvent } from "react";
import type { Business } from "../api";
import { Bubble } from "./Bubbles";
import { CheckIcon, MicIcon, PhoneIcon } from "./icons";
import { callStatus, callerSpoke, elapsed, spokenCount, type CallState } from "./callState";
import { fmtClock, monogram } from "./format";

export function ConnectingScreen({ biz, onCancel }: { biz: Business; onCancel: () => void }) {
  return (
    <div className="center" data-screen="connecting">
      <div className="ringer" aria-hidden="true">
        <div className="ring" />
        <div className="ring" />
        <div className="core"><PhoneIcon size={40} /></div>
      </div>
      <div className="stack">
        <h1>Connecting…</h1>
        <p>Starting a call about {biz.name}</p>
      </div>
      <button className="cancel-btn" onClick={onCancel}>Cancel</button>
    </div>
  );
}

/** Re-render every second while the call clock runs. */
function useNow(running: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!running) return;
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, [running]);
  return now;
}

interface CallProps {
  biz: Business;
  call: CallState;
  onPttDown: () => void;
  onPttUp: () => void;
  onEnd: () => void;
}

export function CallScreen({ biz, call, onPttDown, onPttUp, onEnd }: CallProps) {
  const now = useNow(call.phase === "live");
  const status = callStatus(call);
  const orbVars = { "--bar-speed": status.barSpeed, "--bar-play": status.barsRunning ? "running" : "paused" } as CSSProperties;

  const down = (e: PointerEvent<HTMLButtonElement>) => {
    if (e.button !== 0) return;
    try {
      e.currentTarget.setPointerCapture(e.pointerId);
    } catch {
      // not capturable (synthetic event)
    }
    onPttDown();
  };
  const isKey = (e: KeyboardEvent) => e.key === " " || e.key === "Enter";
  const keyDown = (e: KeyboardEvent<HTMLButtonElement>) => {
    if (!isKey(e)) return;
    e.preventDefault();
    if (!e.repeat) onPttDown();
  };
  const keyUp = (e: KeyboardEvent<HTMLButtonElement>) => {
    if (!isKey(e)) return;
    e.preventDefault();
    onPttUp();
  };

  return (
    <div className="screen" data-screen="call">
      <div className="call-top">
        <div className="live-pill">
          <div className="live-dot" />
          <b>Live</b>
          <span className="mono-font" data-testid="call-timer">{fmtClock(elapsed(call, now))}</span>
        </div>
        <div className="biz-chip">
          <span>{monogram(biz.name)}</span>
          <span className="ellipsis">{biz.name}</span>
        </div>
      </div>

      <div className="orb-area">
        <div className={`orb-wrap${status.tone === "call" ? " call" : ""}`} style={orbVars} data-testid="orb">
          {status.orbActive && (
            <>
              <div className="orb-ring" />
              <div className="orb-ring" />
            </>
          )}
          <div className="orb"><i /><i /><i /><i /><i /></div>
        </div>
        <div className={`status ${status.tone}`} aria-live="polite" data-testid="call-status">{status.text}</div>
      </div>

      <div className="call-log">
        <div className="bubbles">
          {call.messages.map((m) => <Bubble key={m.id} m={m} />)}
          {call.botState === "thinking" && !call.talking && (
            <div className="thinking" data-testid="thinking">
              <div />
              <div className="dots"><i /><i /><i /></div>
            </div>
          )}
          {call.talking && (
            <div className="listening" data-testid="listening">
              <div className="wave"><i /><i /><i /></div>
              Listening…
            </div>
          )}
        </div>
      </div>

      <div className="call-controls">
        <button className={`ptt${call.talking ? " talking" : ""}`} aria-label="Push to talk — hold to speak"
          aria-pressed={call.talking}
          onPointerDown={down} onPointerUp={onPttUp} onPointerCancel={onPttUp} onBlur={onPttUp}
          onKeyDown={keyDown} onKeyUp={keyUp} onContextMenu={(e) => e.preventDefault()}>
          {call.talking ? (
            <>
              <div className="wave"><i /><i /><i /><i /></div>
              <span>Release to send</span>
            </>
          ) : (
            <>
              <MicIcon size={22} />
              <span>Hold to talk</span>
            </>
          )}
        </button>
        <button className="end-btn" aria-label="End call" onClick={onEnd}>
          <PhoneIcon size={26} />
        </button>
      </div>
    </div>
  );
}

interface EndedProps {
  biz: Business;
  call: CallState;
  onRead: () => void;
  onBack: () => void;
}

export function EndedScreen({ biz, call, onRead, onBack }: EndedProps) {
  return (
    <div className="center ended" data-screen="ended">
      <div className="done-icon"><CheckIcon size={36} stroke={2.6} /></div>
      <div className="stack">
        <h1>Call ended</h1>
        <p>Saved to {biz.name}’s conversations</p>
        {call.endReason && <p role="status">The call was closed by the server: {call.endReason}</p>}
      </div>
      <div className="stats">
        <div className="stat">
          <div data-testid="ended-duration">{fmtClock(elapsed(call, Date.now()))}</div>
          <div>Duration</div>
        </div>
        <div className="stat">
          <div data-testid="ended-messages">{spokenCount(call)}</div>
          <div>Messages</div>
        </div>
      </div>
      <div className="ended-actions">
        {/* a call nobody spoke in isn't saved for reading (the API hides it) */}
        <button className="btn-pill btn-accent" onClick={onRead} disabled={!call.conversationId || !callerSpoke(call)}>
          {callerSpoke(call) ? "Read this conversation" : "Nothing was said"}
        </button>
        <button className="btn-pill btn-ghost" onClick={onBack}>Back to conversations</button>
      </div>
    </div>
  );
}


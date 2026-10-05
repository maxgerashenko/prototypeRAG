import { MicSmallIcon } from "./icons";
import { fmtClock } from "./format";

export interface BubbleMessage {
  id: string | number;
  role: "bot" | "user" | "system";
  text: string;
  at: number; // seconds since the call started
  latency?: string; // bot: "1.4" -> "· 1.4 s"
  latencyTitle?: string; // bot: all turn timings, shown on hover
  interrupted?: boolean; // bot: cut off by the caller
}

/** "Assistant · 0:12 · 1.4 s · interrupted", like main's page. */
export function botMeta(m: Pick<BubbleMessage, "at" | "latency" | "interrupted">): string {
  let t = `Assistant · ${fmtClock(m.at)}`;
  if (m.latency != null) t += ` · ${m.latency} s`;
  if (m.interrupted) t += " · interrupted";
  return t;
}

export function BotAvatar() {
  return (
    <div className="bot-avatar" aria-hidden="true">
      <i />
      <i />
      <i />
    </div>
  );
}

export function Bubble({ m }: { m: BubbleMessage }) {
  if (m.role === "system") {
    return <div className="pill error" role="status">{m.text}</div>;
  }
  if (m.role === "bot") {
    return (
      <div className="msg" data-role="bot">
        <div className="msg-bot">
          <BotAvatar />
          <div className="msg-col">
            <div className="bubble-bot">{m.text}</div>
            <div className="msg-meta" title={m.latencyTitle}>{botMeta(m)}</div>
          </div>
        </div>
      </div>
    );
  }
  return (
    <div className="msg" data-role="user">
      <div className="msg-user">
        <div className="bubble-user">{m.text}</div>
        <div className="msg-meta">
          <MicSmallIcon />
          You · {fmtClock(m.at)}
        </div>
      </div>
    </div>
  );
}

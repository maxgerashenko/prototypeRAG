import type { ConversationDetail } from "../api";
import type { Loadable } from "./App";
import { Bubble } from "./Bubbles";
import { ChevronLeft, PhoneIcon } from "./icons";
import { fmtClock, fmtWhen, plural } from "./format";

interface Props {
  bizName: string;
  convo: Loadable<ConversationDetail>;
  onBack: () => void;
  onContinue: () => void;
}

export function TranscriptScreen({ bizName, convo, onBack, onContinue }: Props) {
  const c = convo.data;
  const dur = c ? fmtClock(c.duration_s) : "";
  const when = c ? fmtWhen(c.started_at) : "";
  const kind = c?.channel === "chat" ? "Chat" : "Call";
  return (
    <div className="screen slide" data-screen="transcript">
      <div className="back-row">
        <button className="back-btn" aria-label="Back" onClick={onBack}>
          <ChevronLeft />
          <span className="ellipsis">{bizName}</span>
        </button>
      </div>
      <div className="tr-head">
        <h1>{c ? c.title : convo.status === "error" ? "Conversation" : "Loading…"}</h1>
        {c && (
          <div className="tr-meta">
            <span>{when}</span>
            <span>·</span>
            <span>{dur}</span>
            <span>·</span>
            <span>{plural(c.messages.length, "message", "messages")}</span>
          </div>
        )}
      </div>

      <div className="scroll">
        <div className="bubbles">
          {convo.status === "error" && <div className="pill error" role="alert">Couldn’t load this conversation ({convo.error}).</div>}
          {c && (
            <>
              <div className="pill">{kind} started · {when}</div>
              {c.messages.map((m, i) => (
                <Bubble key={i} m={{ id: i, role: m.role === "user" ? "user" : "bot", text: m.content, at: m.at_s }} />
              ))}
              {c.messages.length === 0 && <div className="pill">No messages were recorded</div>}
              <div className="pill">{kind} ended · {dur}</div>
            </>
          )}
        </div>
      </div>

      <div className="footer">
        <button className="btn-pill btn-call" onClick={onContinue}>
          <PhoneIcon />
          Continue in a new call
        </button>
      </div>
    </div>
  );
}

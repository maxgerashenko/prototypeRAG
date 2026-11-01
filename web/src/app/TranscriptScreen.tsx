import type { ConversationDetail } from "../api";
import type { Loadable } from "./App";
import { Bubble } from "./Bubbles";
import { GlobeIcon } from "./icons";
import { OutcomeBadge } from "./OutcomeBadge";
import { PullRegion, Skeleton, useMinShow } from "./PullIndicator";
import type { PullState } from "./pullToRefresh";
import { SourceBadge } from "./SourceIcon";
import { ViewHeader } from "./ViewHeader";
import { fmtClock, fmtWhen, plural } from "./format";

interface Props {
  pull: PullState;
  bizName: string;
  convo: Loadable<ConversationDetail>;
  onBack: () => void;
  onContinue: () => void;
}

export function TranscriptScreen({ pull, bizName, convo, onBack, onContinue }: Props) {
  const c = convo.data;
  const dur = c ? fmtClock(c.duration_s) : "";
  const when = c ? fmtWhen(c.started_at) : "";
  const loading = !c && convo.status !== "error";
  const showSk = useMinShow(loading);
  const kind = c?.channel === "chat" ? "Chat" : "Call";
  return (
    <div className="screen slide" data-screen="transcript">
      <div className="tr-head">
        <ViewHeader
          name={bizName}
          title={c && !showSk ? c.title : convo.status === "error" ? "Conversation" : (<span className="sk-block sk-title" aria-hidden="true" />)}
          sub={bizName}
          onBack={onBack}
        />
        {c && !showSk && (
          <div className="tr-meta">
            <SourceBadge source={c.source} size={20} />
            <span>{when}</span>
            <span>·</span>
            <span>{dur}</span>
            <span>·</span>
            <span>{plural(c.messages.length, "message", "messages")}</span>
          </div>
        )}
      </div>

      {c && !showSk && (c.summary || c.outcome || c.follow_up) && (
        <div className="tr-summary">
          <OutcomeBadge outcome={c.outcome} followUp={c.follow_up} />
          {c.summary && <p>{c.summary}</p>}
        </div>
      )}

      <PullRegion state={pull}>
      <div className="scroll">
        <div className="bubbles">
          {convo.status === "error" && <div className="pill error" role="alert">Couldn’t load this conversation ({convo.error}).</div>}
          {showSk && <Skeleton kind="messages" label="Loading conversation" />}
          {c && !showSk && (
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
      </PullRegion>

      <div className="footer">
        <button className="btn-pill btn-call" onClick={onContinue}>
          <GlobeIcon />
          Continue in a new call
        </button>
      </div>
    </div>
  );
}

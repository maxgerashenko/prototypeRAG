import type { Business, ConversationSummary } from "../api";
import type { Loadable } from "./App";
import { ChatIcon, ChevronLeft, ClockIcon, PhoneIcon } from "./icons";
import { SourceBadge } from "./SourceIcon";
import { fmtClock, fmtWhen, hostOf, monogram, plural, previewText } from "./format";

interface Props {
  biz: Business;
  convos: Loadable<ConversationSummary[]> | undefined;
  newIds: ReadonlySet<string>;
  onBack: () => void;
  onCall: () => void;
  onOpen: (convoId: string) => void;
}

export function ConversationsScreen({ biz, convos, newIds, onBack, onCall, onOpen }: Props) {
  const list = convos?.data ?? [];
  return (
    <div className="screen slide" data-screen="convos">
      <div className="back-row">
        <button className="back-btn" aria-label="Back to businesses" onClick={onBack}>
          <ChevronLeft />
          Businesses
        </button>
      </div>

      <div className="biz-head">
        <div className="big-mono">{monogram(biz.name)}</div>
        <div style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
          <h1>{biz.name}</h1>
          <div>{hostOf(biz)}</div>
        </div>
      </div>

      <div style={{ padding: "0 20px 20px" }}>
        <button className="btn-pill btn-call" onClick={onCall}>
          <PhoneIcon />
          Start new call
        </button>
      </div>

      <div className="section-head">
        <h2>Previous conversations</h2>
        <span>{convos?.status === "ok" ? list.length : ""}</span>
      </div>

      <div className="scroll convo-list">
        {(!convos || (convos.status === "loading" && !convos.data)) && <p className="notice">Loading conversations…</p>}
        {convos?.status === "error" && <p className="notice" role="alert">Couldn’t load conversations ({convos.error}).</p>}
        {list.map((c, i) => {
          const isNew = newIds.has(c.id);
          return (
            <button key={c.id} className={`convo-card${isNew ? " new" : ""}`} style={{ animationDelay: `${i * 0.06}s` }}
              onClick={() => onOpen(c.id)}>
              <div className="convo-top">
                <SourceBadge source={c.source} />
                <div className="convo-title ellipsis">{c.title}</div>
                {isNew && <span className="badge-new">NEW</span>}
              </div>
              <div className="convo-preview">{previewText(c.preview)}</div>
              <div className="convo-meta">
                <span>{fmtWhen(c.started_at)}</span>
                <span><ClockIcon />{fmtClock(c.duration_s)}</span>
                <span><ChatIcon size={12} stroke={2.2} />{plural(c.message_count, "message", "messages")}</span>
              </div>
            </button>
          );
        })}
        {convos?.status === "ok" && list.length === 0 && (
          <div className="empty">
            <div className="empty-icon"><ChatIcon size={24} /></div>
            <strong>No conversations yet</strong>
            <p>Start a call and it will show up here when you hang up.</p>
          </div>
        )}
      </div>
    </div>
  );
}

import { useEffect, useState } from "react";
import type { BusinessOut, ConversationDetail } from "../api/types";
import { transcriptCache } from "../api/cache";
import { getConversationDetail } from "../api/client";
import { dateLabel, fmtDur } from "../utils/format";
import { BackIcon, Phone20Icon } from "../components/icons";
import { MessageBubble } from "../components/MessageBubble";

interface TranscriptScreenProps {
  business: BusinessOut | null;
  bizId: string;
  convoId: string;
  onBack: () => void;
  onContinue: () => void;
}

export function TranscriptScreen({ business, bizId, convoId, onBack, onContinue }: TranscriptScreenProps) {
  const [, setTick] = useState(0);
  useEffect(() => {
    transcriptCache.load(convoId, () => getConversationDetail(bizId, convoId), () => setTick((t) => t + 1));
  }, [bizId, convoId]);

  const cache = transcriptCache.get(convoId);

  return (
    <div className="screen-slide">
      <div className="convos-header-row">
        <button aria-label="Back" className="back-btn" onClick={onBack}>
          <BackIcon />
          {business ? business.name : ""}
        </button>
      </div>

      <div className="transcript-sub-header">
        <h1>{!cache || cache.status === "loading" ? "Loading…" : cache.status === "error" ? "Conversation" : cache.data!.title}</h1>
        {cache?.status === "ready" && cache.data && (
          <div className="transcript-sub-header__meta">
            <span>{dateLabel(cache.data.started_at)}</span>
            <span>·</span>
            <span>{fmtDur(cache.data.duration_s)}</span>
            <span>·</span>
            <span>
              {cache.data.messages.filter((m) => m.role === "user" || m.role === "assistant").length} messages
            </span>
          </div>
        )}
      </div>

      <div className="transcript-scroll">
        <div className="transcript-scroll__inner">
          {!cache || cache.status === "loading" ? (
            <p className="card-list__empty">Loading…</p>
          ) : cache.status === "error" ? (
            <p className="card-list__empty card-list__empty--error">Couldn't load this conversation.</p>
          ) : (
            <TranscriptBody convo={cache.data as ConversationDetail} />
          )}
        </div>
      </div>

      <div className="transcript-footer">
        <button className="start-call-btn" onClick={onContinue}>
          <Phone20Icon />
          Continue in a new call
        </button>
      </div>
    </div>
  );
}

function TranscriptBody({ convo }: { convo: ConversationDetail }) {
  const data = convo;
  const dLabel = dateLabel(data.started_at);
  const durLabel = fmtDur(data.duration_s);
  const msgs = data.messages.filter((m) => m.role === "user" || m.role === "assistant");

  return (
    <>
      <div className="transcript-marker">Call started · {dLabel}</div>
      {msgs.map((m, i) => (
        <MessageBubble
          key={i}
          message={{ role: m.role as "user" | "assistant", text: m.content, time: fmtDur(m.at_s) }}
        />
      ))}
      <div className="transcript-marker">Call ended · {durLabel}</div>
    </>
  );
}

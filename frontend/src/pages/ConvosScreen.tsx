import { useEffect, useState } from "react";
import type { BusinessOut, ConversationSummary } from "../api/types";
import { convoCache } from "../api/cache";
import { getConversations } from "../api/client";
import { dateLabel, fmtDur, hostOf, initials } from "../utils/format";
import { BackIcon, Chat12Icon, Chat24Icon, ClockIcon, Phone20Icon } from "../components/icons";

interface ConvosScreenProps {
  business: BusinessOut | null;
  bizId: string;
  lastEndedId: string | null;
  onBack: () => void;
  onStartCall: () => void;
  onOpenTranscript: (convoId: string) => void;
}

export function ConvosScreen({ business, bizId, lastEndedId, onBack, onStartCall, onOpenTranscript }: ConvosScreenProps) {
  const [, setTick] = useState(0);
  useEffect(() => {
    convoCache.load(bizId, () => getConversations(bizId), () => setTick((t) => t + 1));
  }, [bizId]);

  const cache = convoCache.get(bizId);
  const list: ConversationSummary[] = cache?.status === "ready" ? (cache.data ?? []) : [];

  return (
    <div className="screen-slide">
      <div className="convos-header-row">
        <button aria-label="Back to businesses" className="back-btn" onClick={onBack}>
          <BackIcon />
          Businesses
        </button>
      </div>

      <div className="biz-header">
        <div className="biz-header__avatar">{business ? initials(business.name) : "?"}</div>
        <div className="biz-header__col">
          <h1 className="biz-header__name">{business ? business.name : ""}</h1>
          <div className="biz-header__host">{business ? hostOf(business.website) : ""}</div>
        </div>
      </div>

      <div className="start-call-wrap">
        <button className="start-call-btn" onClick={onStartCall}>
          <Phone20Icon />
          Start new call
        </button>
      </div>

      <div className="convos-section-header">
        <h2>Previous conversations</h2>
        <span className="convos-section-header__count">{cache?.status === "ready" ? list.length : ""}</span>
      </div>

      <div className="convos-list">
        {!cache || cache.status === "loading" ? (
          <p className="card-list__empty">Loading…</p>
        ) : cache.status === "error" ? (
          <p className="card-list__empty card-list__empty--error">Couldn't load conversations</p>
        ) : list.length === 0 ? (
          <div className="convos-empty">
            <div className="convos-empty__icon">
              <Chat24Icon />
            </div>
            <div className="convos-empty__title">No conversations yet</div>
            <div className="convos-empty__body">Start a call and it will show up here when you hang up.</div>
          </div>
        ) : (
          list.map((c, i) => (
            <ConvoCard key={c.id} convo={c} index={i} isNew={c.id === lastEndedId} onClick={() => onOpenTranscript(c.id)} />
          ))
        )}
      </div>
    </div>
  );
}

function ConvoCard({
  convo,
  index,
  isNew,
  onClick,
}: {
  convo: ConversationSummary;
  index: number;
  isNew: boolean;
  onClick: () => void;
}) {
  return (
    <button
      className={"convo-card" + (isNew ? " convo-card--new" : "")}
      style={{ animationDelay: index * 0.06 + "s" }}
      onClick={onClick}
    >
      <div className="convo-card__top">
        <div className="convo-card__title">{convo.title}</div>
        {isNew && <span className="convo-card__new-badge">NEW</span>}
      </div>
      <div className="convo-card__preview">{convo.preview}</div>
      <div className="convo-card__meta">
        <span>{dateLabel(convo.started_at)}</span>
        <span className="convo-card__meta-item">
          <ClockIcon />
          {fmtDur(convo.duration_s)}
        </span>
        <span className="convo-card__meta-item">
          <Chat12Icon />
          {convo.message_count} msgs
        </span>
      </div>
    </button>
  );
}

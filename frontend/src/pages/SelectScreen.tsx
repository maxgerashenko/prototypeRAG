import { useEffect, useRef, useState } from "react";
import type { BusinessOut, ConversationSummary } from "../api/types";
import { convoCache } from "../api/cache";
import { getConversations } from "../api/client";
import { countLabel, dateLabel, fmtDur, hostOf, initials } from "../utils/format";
import { CheckIcon, Chat15Icon, ChevronIcon, MicHeaderIcon, Phone22Icon, SearchIcon } from "../components/icons";

interface SelectScreenProps {
  businesses: BusinessOut[] | null;
  businessesError: boolean;
  bizId: string | null;
  query: string;
  onQueryChange: (q: string) => void;
  onPickBusiness: (id: string) => void;
  onStartCall: (id: string) => void;
  onSeeAll: (bizId: string) => void;
  onOpenTranscript: (bizId: string, convoId: string) => void;
  lastEndedId: string | null;
  errorMessage: string;
}

export function SelectScreen(props: SelectScreenProps) {
  const { businesses, businessesError, bizId, query, lastEndedId } = props;
  const listRef = useRef<HTMLDivElement | null>(null);
  const cardRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const [, setTick] = useState(0);

  useEffect(() => {
    if (!bizId) return;
    convoCache.load(bizId, () => getConversations(bizId), () => setTick((t) => t + 1));
  }, [bizId]);

  // the 40 ms scroll race: schedule after the card's recent panel has had a chance to
  // open, but bail if this screen (or the selection) is gone by then.
  useEffect(() => {
    if (!bizId) return;
    const id = bizId;
    const timer = setTimeout(() => {
      const list = listRef.current;
      const el = cardRefs.current[id];
      if (!list || !el) return;
      const lr = list.getBoundingClientRect();
      const er = el.getBoundingClientRect();
      const top = list.scrollTop + (er.top - lr.top) - 2;
      if (list.scrollTo) list.scrollTo({ top: Math.max(0, top), behavior: "smooth" });
      else list.scrollTop = Math.max(0, top);
    }, 40);
    return () => clearTimeout(timer);
  }, [bizId]);

  const currentBiz = businesses?.find((b) => b.id === bizId) ?? null;
  const q = query.trim().toLowerCase();
  const filtered =
    businesses?.filter(
      (b) => b.id === bizId || !q || b.name.toLowerCase().includes(q) || hostOf(b.website).toLowerCase().includes(q),
    ) ?? [];

  return (
    <div className="screen-rise">
      <div className="select-header">
        <div className="select-header__eyebrow">
          <MicHeaderIcon />
          Voice assistant
        </div>
        <h1>Talk about a business, out loud</h1>
        <p>Pick a business and call the assistant. Every call is saved as a chat you can read later.</p>
      </div>

      <div className="search-wrap">
        <label className="search-label">
          <SearchIcon />
          <input
            className="search-input"
            aria-label="Search businesses"
            placeholder="Search businesses"
            value={query}
            onChange={(e) => props.onQueryChange(e.target.value)}
          />
        </label>
      </div>

      <div className="card-list" ref={listRef}>
        {businessesError ? (
          <p className="card-list__empty card-list__empty--error">Couldn't load businesses</p>
        ) : businesses === null ? (
          <p className="card-list__empty">Loading…</p>
        ) : (
          <>
            {filtered.map((b) => (
              <BusinessCard
                key={b.id}
                ref={(el) => {
                  cardRefs.current[b.id] = el;
                }}
                business={b}
                selected={b.id === bizId}
                lastEndedId={lastEndedId}
                onPick={() => props.onPickBusiness(b.id)}
                onSeeAll={() => props.onSeeAll(b.id)}
                onOpenTranscript={(convoId) => props.onOpenTranscript(b.id, convoId)}
              />
            ))}
            {filtered.length === 0 && <p className="card-list__empty">No businesses match your search.</p>}
            {bizId && <div aria-hidden="true" className="card-list__spacer" />}
          </>
        )}
      </div>

      <div className="select-bottom">
        <div className="select-bottom__call-wrap">
          {currentBiz && <div aria-hidden="true" className="select-call-halo" />}
          <button
            className={"select-call-btn " + (currentBiz ? "select-call-btn--active" : "select-call-btn--idle")}
            disabled={!currentBiz}
            onClick={() => {
              if (bizId) props.onStartCall(bizId);
            }}
          >
            <Phone22Icon />
            <span className="select-call-btn__label">
              {currentBiz ? "Call about " + currentBiz.name : "Select a business to call"}
            </span>
          </button>
        </div>
        {props.errorMessage && <div className="select-error-line">{props.errorMessage}</div>}
      </div>
    </div>
  );
}

interface BusinessCardProps {
  business: BusinessOut;
  selected: boolean;
  lastEndedId: string | null;
  onPick: () => void;
  onSeeAll: () => void;
  onOpenTranscript: (convoId: string) => void;
  ref: (el: HTMLDivElement | null) => void;
}

function BusinessCard({ business, selected, lastEndedId, onPick, onSeeAll, onOpenTranscript, ref }: BusinessCardProps) {
  const mono = initials(business.name);
  return (
    <div ref={ref} className={"biz-card" + (selected ? " biz-card--selected" : "")}>
      <button className="biz-card__btn" aria-pressed={String(selected)} aria-expanded={String(selected)} onClick={onPick}>
        <div className={"biz-card__mono" + (selected ? " biz-card__mono--selected" : "")}>{mono}</div>
        <div className="biz-card__body">
          <div className="biz-card__name">{business.name}</div>
          <div className="biz-card__sub">
            {hostOf(business.website)} · {countLabel(business.conversation_count)}
          </div>
        </div>
        <div className={"biz-card__radio" + (selected ? " biz-card__radio--selected" : "")}>
          {selected && <CheckIcon />}
        </div>
      </button>
      {selected && (
        <RecentPanel business={business} lastEndedId={lastEndedId} onSeeAll={onSeeAll} onOpenTranscript={onOpenTranscript} />
      )}
    </div>
  );
}

function RecentPanel({
  business,
  lastEndedId,
  onSeeAll,
  onOpenTranscript,
}: {
  business: BusinessOut;
  lastEndedId: string | null;
  onSeeAll: () => void;
  onOpenTranscript: (convoId: string) => void;
}) {
  const cache = convoCache.get(business.id);
  const list: ConversationSummary[] = cache?.status === "ready" ? (cache.data ?? []) : [];

  return (
    <div className="recent-panel">
      <div className="recent-panel__header">
        <span className="recent-panel__title">Recent conversations</span>
        <span className="recent-panel__count">{cache?.status === "ready" ? countLabel(list.length) : ""}</span>
      </div>
      {!cache || cache.status === "loading" ? (
        <div className="recent-panel__status">Loading…</div>
      ) : cache.status === "error" ? (
        <div className="recent-panel__status recent-panel__status--error">Couldn't load conversations</div>
      ) : list.length === 0 ? (
        <div className="recent-panel__empty">No conversations yet. Start a call below and it will show up here.</div>
      ) : (
        <>
          {list.slice(0, 3).map((c) => (
            <RecentRow key={c.id} convo={c} isNew={c.id === lastEndedId} onClick={() => onOpenTranscript(c.id)} />
          ))}
          {list.length > 3 && (
            <button className="recent-panel__more" onClick={onSeeAll}>
              See all {list.length} conversations
            </button>
          )}
        </>
      )}
    </div>
  );
}

function RecentRow({ convo, isNew, onClick }: { convo: ConversationSummary; isNew: boolean; onClick: () => void }) {
  return (
    <button className="recent-row" onClick={onClick}>
      <div className="recent-row__icon">
        <Chat15Icon />
      </div>
      <div className="recent-row__body">
        <div className="recent-row__title-line">
          <span className="recent-row__title">{convo.title}</span>
          {isNew && <span className="recent-row__new">NEW</span>}
        </div>
        <div className="recent-row__sub">
          {dateLabel(convo.started_at)} · {fmtDur(convo.duration_s)} · {convo.message_count} msgs
        </div>
      </div>
      <ChevronIcon />
    </button>
  );
}

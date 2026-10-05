import { useRef } from "react";
import type { Business, ConversationSummary } from "../api";
import type { Loadable } from "./App";
import { ChatIcon, CheckIcon, ChevronRight, MicIcon, PhoneIcon, SearchIcon } from "./icons";
import { chatsLabel, durationSeconds, fmtClock, fmtWhen, matchesQuery, monogram, plural } from "./format";

const RECENT = 3;

interface Props {
  businesses: Loadable<Business[]>;
  convos: Record<string, Loadable<ConversationSummary[]>>;
  newIds: ReadonlySet<string>;
  query: string;
  selId: string | null;
  banner: string | null;
  onQuery: (q: string) => void;
  onPick: (id: string | null) => void;
  onOpen: (bizId: string, convoId: string) => void;
  onSeeAll: (bizId: string) => void;
  onCall: (bizId: string) => void;
  onRetry: () => void;
  onDismissBanner: () => void;
}

export function SelectScreen(p: Props) {
  const listRef = useRef<HTMLDivElement>(null);
  const cardRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const all = p.businesses.data ?? [];
  const items = all.filter((b) => b.id === p.selId || matchesQuery(b, p.query));
  const selBiz = all.find((b) => b.id === p.selId) ?? null;

  function scrollToCard(id: string) {
    const list = listRef.current;
    const el = cardRefs.current[id];
    if (!list || !el) return;
    const top = list.scrollTop + el.getBoundingClientRect().top - list.getBoundingClientRect().top - 2;
    if (list.scrollTo) list.scrollTo({ top: Math.max(0, top), behavior: "smooth" });
    else list.scrollTop = Math.max(0, top);
  }

  function pick(id: string) {
    const on = id === p.selId;
    p.onPick(on ? null : id);
    if (!on) window.setTimeout(() => scrollToCard(id), 40);
  }

  return (
    <div className="screen rise" data-screen="select">
      <div className="sel-head">
        <div className="eyebrow">
          <MicIcon />
          Voice assistant
        </div>
        <h1>Talk about a business, out loud</h1>
        <p>Pick a business and call the assistant. Every call is saved as a chat you can read later.</p>
      </div>

      {p.banner && (
        <div className="banner" role="alert">
          <span>{p.banner}</span>
          <button onClick={p.onDismissBanner} aria-label="Dismiss">✕</button>
        </div>
      )}

      <div className="search-wrap">
        <label className="search">
          <SearchIcon />
          <input aria-label="Search businesses" placeholder="Search businesses" value={p.query}
            onChange={(e) => p.onQuery(e.target.value)} />
        </label>
      </div>

      <div className="scroll biz-list" ref={listRef}>
        {p.businesses.status === "loading" && <p className="notice">Loading businesses…</p>}
        {p.businesses.status === "error" && (
          <p className="notice" role="alert">
            Couldn’t load businesses ({p.businesses.error}).{" "}
            <button className="see-all" onClick={p.onRetry}>Try again</button>
          </p>
        )}
        {p.businesses.status === "ok" && all.length === 0 && (
          <p className="notice">
            No businesses yet. Crawl one first:
            <br />
            <code>uv run python -m app.ingest.run --url &lt;site&gt;</code>
          </p>
        )}

        {items.map((b) => {
          const on = b.id === p.selId;
          const convos = p.convos[b.id];
          const n = convos?.data ? convos.data.length : b.conversation_count;
          const countLabel = chatsLabel(n);
          return (
            <div key={b.id} className={`biz-card${on ? " on" : ""}`} data-testid="biz-card"
              ref={(el) => { cardRefs.current[b.id] = el; }}>
              <button className="biz-pick" aria-pressed={on} aria-expanded={on} onClick={() => pick(b.id)}>
                <div className="biz-mono">{monogram(b.name)}</div>
                <div className="biz-text">
                  <div className="biz-name ellipsis">{b.name}</div>
                  <div className="biz-sub">{b.category} · {countLabel}</div>
                </div>
                <div className="radio">{on && <CheckIcon />}</div>
              </button>

              {on && (
                <div className="recent">
                  <div className="recent-head">
                    <span>Recent conversations</span>
                    <span>{countLabel}</span>
                  </div>
                  {convos?.status === "loading" && !convos.data && <div className="recent-empty">Loading…</div>}
                  {convos?.status === "error" && (
                    <div className="recent-empty" role="alert">Couldn’t load conversations ({convos.error}).</div>
                  )}
                  {convos?.data?.slice(0, RECENT).map((c) => (
                    <button key={c.id} className="recent-row" onClick={() => p.onOpen(b.id, c.id)}>
                      <div className="recent-icon"><ChatIcon /></div>
                      <div className="recent-text">
                        <div className="recent-title">
                          <span className="ellipsis">{c.title}</span>
                          {p.newIds.has(c.id) && <span className="badge-new small">NEW</span>}
                        </div>
                        <div className="recent-meta">
                          {fmtWhen(c.started_at)} · {fmtClock(durationSeconds(c))} · {plural(c.message_count, "msg", "msgs")}
                        </div>
                      </div>
                      <ChevronRight />
                    </button>
                  ))}
                  {convos?.status === "ok" && n === 0 && (
                    <div className="recent-empty">No conversations yet. Start a call below and it will show up here.</div>
                  )}
                  {n > RECENT && convos?.status === "ok" && (
                    <button className="see-all" onClick={() => p.onSeeAll(b.id)}>See all {n} conversations</button>
                  )}
                </div>
              )}
            </div>
          );
        })}

        {p.businesses.status === "ok" && all.length > 0 && items.length === 0 && (
          <p className="notice">No businesses match your search.</p>
        )}
        {selBiz && <div className="spacer" aria-hidden="true" />}
      </div>

      <div className="footer">
        <div className="call-wrap">
          {selBiz && <div className="halo" />}
          <button className="call-btn" disabled={!selBiz} onClick={() => selBiz && p.onCall(selBiz.id)}>
            <PhoneIcon size={22} />
            <span className="ellipsis">{selBiz ? `Call about ${selBiz.name}` : "Select a business to call"}</span>
          </button>
        </div>
      </div>
    </div>
  );
}

import { useRef } from "react";
import type { Business, ConversationSummary } from "../api";
import type { Loadable } from "./App";
import { ChevronDown, ChevronRight, GlobeIcon, MicIcon, PhoneIcon, SearchIcon } from "./icons";
import { SkeletonGate } from "./PullIndicator";
import { SourceBadge } from "./SourceIcon";
import { chatsLabel, fmtClock, fmtPhone, fmtWhen, hostOf, matchesQuery, monogram, plural } from "./format";

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
  /** The main Twilio number (shown as the title); null keeps the plain header. */
  mainNumber: string | null;
  onRetry: () => void;
  onDismissBanner: () => void;
}

export function SelectScreen(p: Props) {
  const listRef = useRef<HTMLDivElement>(null);
  const cardRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const all = p.businesses.data ?? [];
  const items = all.filter((b) => b.id === p.selId || matchesQuery(b, p.query));
  const selBiz = all.find((b) => b.id === p.selId) ?? null;
  const holder = p.mainNumber ? all.find((b) => b.phone_numbers.includes(p.mainNumber!)) ?? null : null;

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
      {p.mainNumber ? (
        <div className="sel-head">
          <div className="eyebrow">
            <PhoneIcon size={16} />
            Call by phone
          </div>
          <h1>
            <a className="num-link" href={`tel:${p.mainNumber}`} aria-label={`Call ${p.mainNumber}`}>
              {fmtPhone(p.mainNumber)}
            </a>
          </h1>
          <p data-testid="num-note">
            {holder ? <>Calls to this number reach <strong>{holder.name}</strong>. </> : "No business has this number yet. "}
            Pick a business below to point the number at it. On a phone, tap the number to call; the
            button at the bottom calls from this browser.
          </p>
        </div>
      ) : (
        <div className="sel-head">
          <div className="eyebrow">
            <MicIcon />
            Voice assistant
          </div>
          <h1>Talk about a business, out loud</h1>
          <p>Pick a business and call the assistant. Every call is saved as a chat you can read later.</p>
        </div>
      )}

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
        <SkeletonGate loading={p.businesses.status === "loading" && !p.businesses.data} kind="businesses" label="Loading businesses">
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
                  <div className="biz-sub">{hostOf(b)} · {countLabel}</div>
                  {b.phone_numbers.length > 0 && (
                    <div className="biz-num" data-testid="biz-num">
                      <PhoneIcon size={12} />
                      <span className="ellipsis">{b.phone_numbers.map(fmtPhone).join(", ")}</span>
                    </div>
                  )}
                </div>
                <span className="biz-chev"><ChevronDown /></span>
              </button>

              {on && (
                <div className="recent">
                  <div className="recent-head">
                    <span>Recent conversations</span>
                    <span>{countLabel}</span>
                  </div>
                  <SkeletonGate loading={convos?.status === "loading" && !convos.data} kind="recent" label="Loading conversations">
                  {convos?.status === "error" && (
                    <div className="recent-empty" role="alert">Couldn’t load conversations ({convos.error}).</div>
                  )}
                  {convos?.data?.slice(0, RECENT).map((c) => (
                    <button key={c.id} className="recent-row" onClick={() => p.onOpen(b.id, c.id)}>
                      <SourceBadge source={c.source} size={15} />
                      <div className="recent-text">
                        <div className="recent-title">
                          <span className="ellipsis">{c.title}</span>
                          {p.newIds.has(c.id) && <span className="badge-new small">NEW</span>}
                        </div>
                        <div className="recent-meta">
                          {fmtWhen(c.started_at)} · {fmtClock(c.duration_s)} · {plural(c.message_count, "msg", "msgs")}
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
                  </SkeletonGate>
                </div>
              )}
            </div>
          );
        })}

        {p.businesses.status === "ok" && all.length > 0 && items.length === 0 && (
          <p className="notice">No businesses match your search.</p>
        )}
        </SkeletonGate>
        {selBiz && <div className="spacer" aria-hidden="true" />}
      </div>

      <div className="footer">
        <div className="call-wrap">
          {selBiz && <div className="halo" />}
          <button className="call-btn" disabled={!selBiz} onClick={() => selBiz && p.onCall(selBiz.id)}>
            <GlobeIcon size={22} />
            <span className="ellipsis">{selBiz ? `Call about ${selBiz.name}` : "Select a business to call"}</span>
          </button>
        </div>
      </div>
    </div>
  );
}

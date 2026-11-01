import { useEffect, useRef, useState } from "react";
import type { Business } from "../api";
import { ChevronDown, PhoneIcon } from "./icons";
import { NumberSheet } from "./NumberSheet";
import { ViewHeader } from "./ViewHeader";
import { StatusChip } from "./StatusChip";
import { PullRegion, useMinShow } from "./PullIndicator";
import type { PullState } from "./pullToRefresh";
import { fmtPhone, hostOf } from "./format";
import { GREETING_PREFIX, barWidth, capsLabel, greetingView, questionsSummary } from "./settings";
import type { SettingsBindings } from "./useBusinessSettings";

interface Props extends SettingsBindings {
  biz: Business;
  pull: PullState;
  onBack: () => void;
}

/** Placeholder lines in a card while its data loads (same blocks as the list skeletons). */
function CardSkeleton({ label, rows, bars }: { label: string; rows: number; bars?: boolean }) {
  return (
    <div className="skeleton set-skeleton" role="status" aria-busy="true">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="sk-lines">
          <div className="sk-block sk-line" style={{ width: `${88 - ((i * 19) % 40)}%` }} />
          {bars && <div className="sk-block sk-line short" style={{ width: "100%", height: 6 }} />}
        </div>
      ))}
      {!bars && <div className="sk-block sk-line short" />}
      <span className="sr-only">{label}</span>
    </div>
  );
}

export function SettingsScreen(p: Props) {
  const { biz, greeting } = p;
  const saved = greeting.data?.text ?? "";
  const [draft, setDraft] = useState(saved);
  const [greetOpen, setGreetOpen] = useState(false); // collapsed: the spoken text read-only; open: edit mode
  const [openQ, setOpenQ] = useState(0); // one answer open at a time, the first by default
  useEffect(() => setDraft(saved), [greeting.data, biz.id]); // the server answered (loaded, saved, other business)
  useEffect(() => { setOpenQ(0); setGreetOpen(false); }, [biz.id]);
  // a save that finished without an error folds the card back to the read-only view
  const wasSaving = useRef(false);
  useEffect(() => {
    if (wasSaving.current && !p.savingGreeting && !p.greetingError) setGreetOpen(false);
    wasSaving.current = p.savingGreeting;
  }, [p.savingGreeting, p.greetingError]);

  const view = greetingView({
    saved, draft, cached: !!greeting.data?.cached, saving: p.savingGreeting, cachedAt: greeting.data?.cached_at,
  });
  const max = greeting.data?.max_length ?? 300;
  const spoken = greeting.data?.full_text ?? `${GREETING_PREFIX} ${saved.trim()}`.trim(); // as the server says it
  const number = biz.phone_numbers[0] ?? null;
  const known = p.numbers.data?.numbers.find((n) => n.number === number);
  const numberSub = ["Twilio", known && capsLabel(known)].filter(Boolean).join(" · ");
  const q = p.questions.data;
  const greetSkeleton = useMinShow(greeting.status === "loading" && !greeting.data);
  const questionsSkeleton = useMinShow(p.questions.status === "loading" && !q);

  return (
    <div className="screen slide screen-settings" data-screen="settings">
      <ViewHeader name={biz.name} title="Settings" sub={<>{biz.name} · {hostOf(biz)}</>} onBack={p.onBack} />

      <PullRegion state={p.pull} area="settings">
      <div className="scroll set-scroll">
        <section className="set-card num-card tap" aria-label="Phone number" onClick={p.onOpenSheet}>
          <div className="num-icon"><PhoneIcon size={17} /></div>
          {number ? (
            <>
              <div className="num-text">
                <span className="set-mono num-main" data-testid="settings-number">{fmtPhone(number)}</span>
                <span className="num-sub">{numberSub}</span>
              </div>
              <button className="btn-outline" onClick={(e) => { e.stopPropagation(); p.onOpenSheet(); }}>Change</button>
            </>
          ) : (
            <>
              <div className="num-text">
                <span className="num-title">No number assigned</span>
                <span className="num-sub">Pick one from Twilio</span>
              </div>
              <button className="btn-call-sm" onClick={(e) => { e.stopPropagation(); p.onOpenSheet(); }}>Choose</button>
            </>
          )}
        </section>

        <section className="set-card" aria-label="First greeting">
          <button className="greet-toggle" aria-expanded={greetOpen} aria-controls="greet-body"
            onClick={() => setGreetOpen((o) => !o)}>
            <h2>First greeting</h2>
            {greeting.data && (
              <StatusChip kind={view.kind === "cached" ? "saved" : view.kind === "notCached" ? "warn" : view.kind}
                label={view.chip} testId="greeting-chip" />
            )}
            <span className={`greet-chev${greetOpen ? " open" : ""}`}><ChevronDown /></span>
          </button>
          {greetSkeleton && <CardSkeleton label="Loading greeting" rows={2} />}
          {!greetSkeleton && greeting.status === "error" && !greeting.data && (
            <p className="set-error" role="alert">Couldn’t load the greeting ({greeting.error}).</p>
          )}
          {!greetSkeleton && greeting.data && (
            <div id="greet-body" className={`greet-body${greetOpen ? "" : " tap"}`} onClick={greetOpen ? undefined : () => setGreetOpen(true)}>
              {greetOpen ? (
                <>
                  <div className={`greet-box${view.dirty ? " dirty" : ""}`}>
                    <div className="greet-legal" data-testid="greeting-legal">{GREETING_PREFIX}</div>
                    <textarea className="greet-input" aria-label="First greeting message" rows={3} placeholder="How can I help you today?"
                      maxLength={max} value={draft} disabled={p.savingGreeting} onChange={(e) => setDraft(e.target.value)} />
                  </div>
                  <p className="greet-hint" data-testid="greeting-hint">
                    The first sentence is added to every call by the app. Your text follows it.
                  </p>
                  {p.greetingError && <p className="set-error" role="alert">{p.greetingError}</p>}
                </>
              ) : (
                <p className="greet-read" data-testid="greeting-read">
                  {spoken}
                </p>
              )}
              <div className="greet-foot">
                <span className="greet-note">{view.count}/{max} · {view.note}</span>
                {greetOpen && (
                  <div className="greet-actions">
                    {view.dirty && !p.savingGreeting && (
                      <button className="btn-outline small" onClick={() => setDraft(saved)}>Reset</button>
                    )}
                    <button className={`btn-accent-sm${p.savingGreeting ? " busy" : ""}`} disabled={view.saveDisabled}
                      onClick={() => p.onSaveGreeting(draft)}>
                      {p.savingGreeting && <span className="spin lg" />}
                      <span>{view.saveLabel}</span>
                    </button>
                  </div>
                )}
              </div>
            </div>
          )}
        </section>

        <section className="set-card q-card" aria-label="Top asked questions">
          <div className="set-card-head base">
            <h2>Top questions</h2>
            {q && <span>{questionsSummary(q.total_questions, q.conversations, q.days)}</span>}
          </div>
          {questionsSkeleton && <CardSkeleton label="Loading questions" rows={3} bars />}
          {!questionsSkeleton && p.questions.status === "error" && !q && (
            <p className="set-error q-empty" role="alert">Couldn’t load the questions ({p.questions.error}).</p>
          )}
          {!questionsSkeleton && q && q.questions.length === 0 && (
            <p className="set-muted q-empty">No questions yet. Callers’ questions will show up here.</p>
          )}
          {!questionsSkeleton && q?.questions.map((item, k) => {
            const open = openQ === k;
            return (
              <div className="q-item" key={k} style={{ animationDelay: `${k * 0.06}s` }}>
                <button className="q-btn" aria-expanded={open} onClick={() => setOpenQ(open ? -1 : k)}>
                  <div className="q-top">
                    <span className="q-rank">{k + 1}</span>
                    <span className="q-text">{item.question}</span>
                    <span className="q-pct">{item.percent}%</span>
                    <span className={`q-chev${open ? " open" : ""}`}><ChevronDown /></span>
                  </div>
                  <div className="q-bar-row">
                    <div className="q-bar">
                      <div className="q-bar-fill" style={{ width: barWidth(item.percent), animationDelay: `${k * 0.06}s` }} />
                    </div>
                    <span className="q-count">{item.count}× asked</span>
                  </div>
                </button>
                {open && (
                  <div className="q-answer">
                    <b>Expected answer</b>
                    <span>{item.answer ?? "No answer was given in these calls."}</span>
                  </div>
                )}
              </div>
            );
          })}
        </section>
      </div>
      </PullRegion>

      {p.sheetOpen && (
        <NumberSheet bizId={biz.id} currentNumber={number} numbers={p.numbers} assigning={p.assigning} error={p.assignError}
          onAssign={p.onAssign} onClose={p.onCloseSheet} />
      )}
    </div>
  );
}

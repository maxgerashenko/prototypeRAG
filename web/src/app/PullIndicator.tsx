import { useEffect, useRef, useState, type ReactNode } from "react";
import { PULL_THRESHOLD, isArmed, pullProgress, type PullState } from "./pullToRefresh";

const BARS = 8;

/** Eight rounded bars in a ring, all the accent blue of the web globe icon (`--accent`). While
 * pulling, bars appear one by one and the ring turns with the finger (`progress` 0..1); while
 * `spinning` the bars fade in turn and the ring rotates (after svg-spinners' bars-rotate-fade). */
export function BarsSpinner({ progress = 1, spinning = false, size = 32 }: { progress?: number; spinning?: boolean; size?: number }) {
  return (
    <svg
      className={"bars-spinner" + (spinning ? " spinning" : "")}
      viewBox="0 0 24 24"
      width={size}
      height={size}
      aria-hidden="true"
      style={spinning ? undefined : { transform: `rotate(${Math.round(progress * 180)}deg)` }}
    >
      {Array.from({ length: BARS }, (_, i) => (
        <rect
          key={i}
          x="10.6" y="1.6" width="2.8" height="6" rx="1.4"
          transform={`rotate(${i * (360 / BARS)} 12 12)`}
          style={{
            fill: "var(--accent)",
            opacity: spinning ? undefined : Math.min(1, Math.max(0, progress * BARS - i)) * 0.9,
            animationDelay: spinning ? `${(i / BARS - 1).toFixed(3)}s` : undefined,
          }}
        />
      ))}
    </svg>
  );
}

/** A list that slides down to reveal a spinner behind it while it is pulled or refreshing.
 * Only the list moves; the screen's header and footer stay put. */
export function PullRegion({ state, children }: { state: PullState; children: ReactNode }) {
  const pulling = state.phase === "pulling";
  const refreshing = state.phase === "refreshing";
  const p = pullProgress(state);
  return (
    <div className="ptr-region">
      {(pulling || refreshing) && (
        <div className={"ptr-under" + (pulling && isArmed(state) ? " armed" : "")} data-testid="ptr" role={refreshing ? "status" : undefined}
          style={{ height: (pulling ? state.distance : PULL_THRESHOLD) / 2 }}>
          <BarsSpinner progress={p} spinning={refreshing} />
          <span className="sr-only">{refreshing ? "Refreshing…" : isArmed(state) ? "Release to refresh" : "Pull to refresh"}</span>
        </div>
      )}
      <div
        className="ptr-layer"
        style={{
          transform: pulling ? `translateY(${state.distance}px)` : refreshing ? `translateY(${PULL_THRESHOLD}px)` : "none",
          transition: pulling ? "none" : "transform .2s ease",
        }}
      >
        {children}
      </div>
    </div>
  );
}

/** Loading placeholder shaped like the content it stands in for. The spinner is kept for pull-to-refresh only. */
export type SkeletonKind = "businesses" | "convos" | "recent" | "messages";

export function Skeleton({ kind, label }: { kind: SkeletonKind; label: string }) {
  const n = { businesses: 4, convos: 4, recent: 3, messages: 5 }[kind];
  return (
    <div className={`skeleton skeleton-${kind}`} role="status" aria-busy="true">
      {Array.from({ length: n }, (_, i) =>
        kind === "messages" ? (
          <div key={i} className={`sk-bubble sk-bubble-${i % 2 ? "user" : "bot"}`} style={{ width: `${55 + ((i * 17) % 30)}%` }} />
        ) : (
          <div key={i} className="sk-row">
            <div className="sk-block sk-icon" />
            <div className="sk-lines">
              <div className="sk-block sk-line" style={{ width: `${60 + ((i * 13) % 30)}%` }} />
              <div className="sk-block sk-line short" />
            </div>
          </div>
        ),
      )}
      <span className="sr-only">{label}</span>
    </div>
  );
}

/** Keeps a skeleton up for at least `minMs` once it has appeared, so a fast load doesn't flash it.
 * Nothing to load (never `loading`) -> false at once, no delay. */
export function useMinShow(loading: boolean, minMs = 600): boolean {
  const [held, setHeld] = useState(loading);
  const since = useRef(loading ? Date.now() : 0);
  useEffect(() => {
    if (loading) {
      if (!held) since.current = Date.now();
      setHeld(true);
      return;
    }
    if (!held) return;
    const t = setTimeout(() => setHeld(false), Math.max(0, minMs - (Date.now() - since.current)));
    return () => clearTimeout(t);
  }, [loading, held, minMs]);
  return loading || held;
}

/** Shows the skeleton while `loading` (and for at least 600 ms after it first showed), else the content. */
export function SkeletonGate({ loading, kind, label, children }: { loading: boolean; kind: SkeletonKind; label: string; children: ReactNode }) {
  return useMinShow(loading) ? <Skeleton kind={kind} label={label} /> : <>{children}</>;
}

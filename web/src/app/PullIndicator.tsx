import type { ReactNode } from "react";
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

/** The same spinner as a loading state: replaces the "Loading…" texts (`small` for inside a row). */
export function Loading({ label, small = false }: { label: string; small?: boolean }) {
  return (
    <div className={"loading" + (small ? " small" : "")} role="status">
      <BarsSpinner spinning size={small ? 22 : 32} />
      <span className="sr-only">{label}</span>
    </div>
  );
}

import type { ReactNode } from "react";
import { PULL_THRESHOLD, isArmed, pullProgress, type PullState } from "./pullToRefresh";

const BARS = 8;

/** Eight rounded bars in a ring, blue to green like the app's accent and call colors. While
 * pulling, bars appear one by one and the ring turns with the finger (`progress` 0..1); while
 * `spinning` the bars fade in turn and the ring rotates (after svg-spinners' bars-rotate-fade). */
export function BarsSpinner({ progress = 1, spinning = false }: { progress?: number; spinning?: boolean }) {
  return (
    <svg
      className={"bars-spinner" + (spinning ? " spinning" : "")}
      viewBox="0 0 24 24"
      width="32"
      height="32"
      aria-hidden="true"
      style={spinning ? undefined : { transform: `rotate(${Math.round(progress * 180)}deg)` }}
    >
      {Array.from({ length: BARS }, (_, i) => (
        <rect
          key={i}
          x="10.6" y="1.6" width="2.8" height="6" rx="1.4"
          transform={`rotate(${i * (360 / BARS)} 12 12)`}
          style={{
            fill: `color-mix(in srgb, var(--accent), var(--call) ${Math.round((i / (BARS - 1)) * 100)}%)`,
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
        <div className={"ptr-under" + (pulling && isArmed(state) ? " armed" : "")} data-testid="ptr" role={refreshing ? "status" : undefined}>
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

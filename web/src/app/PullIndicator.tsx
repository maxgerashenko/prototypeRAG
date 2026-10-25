import { PULL_THRESHOLD, isArmed, pullProgress, type PullState } from "./pullToRefresh";

export function PullIndicator({ state, variant }: { state: PullState; variant: "front" | "behind" }) {
  if (state.phase === "idle" || state.phase === "tracking") {
    return null;
  }

  const p = pullProgress(state);

  if (variant === "front") {
    if (state.phase === "pulling") {
      return (
        <div
          className={"ptr" + (isArmed(state) ? " armed" : "")}
          data-testid="ptr"
          style={{ transform: `translateY(${state.distance - 44}px)`, opacity: p }}
        >
          <div className="ptr-spinner" style={{ transform: `rotate(${Math.round(p * 270)}deg)` }} />
          <span className="sr-only">{isArmed(state) ? "Release to refresh" : "Pull to refresh"}</span>
        </div>
      );
    }
    return (
      <div className="ptr refreshing" data-testid="ptr" role="status" style={{ transform: `translateY(${PULL_THRESHOLD - 44}px)` }}>
        <div className="ptr-spinner" />
        <span className="sr-only">Refreshing…</span>
      </div>
    );
  }

  if (variant === "behind") {
    if (state.phase === "pulling") {
      return (
        <div
          className={"ptr-behind" + (isArmed(state) ? " armed" : "")}
          data-testid="ptr-behind"
          style={{ opacity: p, transform: `scale(${(0.4 + 0.6 * p).toFixed(3)}) rotate(${Math.round(p * 270)}deg)` }}
        >
          <div className="ptr-spinner" />
          <span className="sr-only">{isArmed(state) ? "Release to reload" : "Pull to reload"}</span>
        </div>
      );
    }
    return (
      <div className="ptr-behind refreshing" data-testid="ptr-behind" role="status">
        <div className="ptr-spinner" />
        <span className="sr-only">Reloading…</span>
      </div>
    );
  }

  return null;
}

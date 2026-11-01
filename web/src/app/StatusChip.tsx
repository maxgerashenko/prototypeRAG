import { CheckIcon } from "./icons";

/** Status of something that saves: `saving` (blue, spinner), `dirty` / `warn` (amber, dot), `saved` (green, check).
 *  The look of the greeting card's "Cached" chip; rules in design/StatusChip.md. */
export type ChipKind = "saving" | "dirty" | "warn" | "saved";

export function StatusChip({ kind, label, testId }: { kind: ChipKind; label: string; testId?: string }) {
  const tone = kind === "saved" ? "cached" : kind === "saving" ? "saving" : "warn";
  return (
    <div className={`chip chip-${tone}`} role="status" aria-live="polite" data-testid={testId}>
      {kind === "saving" && <span className="spin" />}
      {kind === "saved" && <CheckIcon size={11} stroke={3} />}
      {(kind === "dirty" || kind === "warn") && <span className="chip-dot" />}
      <span>{label}</span>
    </div>
  );
}

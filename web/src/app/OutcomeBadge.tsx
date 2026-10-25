import { outcomeLabel } from "./format";

interface Props {
  outcome: string | null | undefined;
  followUp: boolean | null | undefined;
}

/** Outcome pill (booked, info request, …) and a "Follow up" mark, for a summarized call. */
export function OutcomeBadge({ outcome, followUp }: Props) {
  const label = outcomeLabel(outcome);
  return (
    <>
      {label && <span className={`badge-outcome outcome-${outcome}`}>{label}</span>}
      {followUp && <span className="badge-follow">Follow up</span>}
    </>
  );
}

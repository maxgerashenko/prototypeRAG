import { reviewLabel } from "./format";

interface Props {
  status: string | null | undefined;
  note: string | null | undefined;
}

/** Review pill (Verified / In testing / Fixed); nothing when the conversation is not checked yet. */
export function ReviewBadge({ status, note }: Props) {
  const label = reviewLabel(status);
  if (!label) return null;
  return <span className={`badge-review review-${status}`} title={note || undefined}>{label}</span>;
}

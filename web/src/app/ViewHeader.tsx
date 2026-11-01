import type { ReactNode } from "react";
import { ChevronLeft } from "./icons";
import { monogram } from "./format";

/** The header every business view starts with (design/ViewHeader.md): the business monogram, the view's name and a grey subtitle, then a small "Swipe to go back" line
 * that is also the click target for mouse users. */
export function ViewHeader({ name, title, sub, hint = "Swipe to go back", onBack }: {
  name: string; title: ReactNode; sub: ReactNode; hint?: string; onBack: () => void;
}) {
  return (
    <>
      <div className="biz-head">
        <div className="big-mono">{monogram(name)}</div>
        <div className="biz-head-text">
          <h1>{title}</h1>
          <div className="ellipsis">{sub}</div>
        </div>
      </div>
      <button className="back-hint" onClick={onBack}>
        <ChevronLeft size={12} stroke={2.6} />
        {hint}
      </button>
    </>
  );
}

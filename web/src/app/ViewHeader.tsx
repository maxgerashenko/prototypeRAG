import type { ReactNode } from "react";
import { monogram } from "./format";

/** The header every business view starts with: business monogram, the view's name as the title and
 * a grey subtitle (design/ViewHeader.md). There is no Back button: swipe right or Esc goes back. */
export function ViewHeader({ name, title, sub }: { name: string; title: ReactNode; sub: ReactNode }) {
  return (
    <div className="biz-head">
      <div className="big-mono">{monogram(name)}</div>
      <div className="biz-head-text">
        <h1>{title}</h1>
        <div className="ellipsis">{sub}</div>
      </div>
    </div>
  );
}

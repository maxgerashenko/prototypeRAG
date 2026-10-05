// Where a conversation came from: green phone for Twilio calls, blue globe for the web app.

import type { ConversationSource } from "../api";
import { GlobeIcon, PhoneIcon } from "./icons";

export const sourceLabel = (source: ConversationSource) => (source === "twilio" ? "Phone" : "Web");

/** Icon only; the label goes in `title`/`aria-label`. */
export function SourceIcon({ source, size = 15 }: { source: ConversationSource; size?: number }) {
  return (
    <span className={`src-icon src-${source}`} role="img" aria-label={sourceLabel(source)} title={sourceLabel(source)}
      data-source={source}>
      {source === "twilio" ? <PhoneIcon size={size} /> : <GlobeIcon size={size} />}
    </span>
  );
}

/** Big icon on a green/blue tinted square, before a conversation's title. */
export function SourceBadge({ source, size = 18 }: { source: ConversationSource; size?: number }) {
  return (
    <div className={`src-badge src-${source}`}>
      <SourceIcon source={source} size={size} />
    </div>
  );
}

// Where a conversation came from: green phone for Twilio calls, blue globe for the web app.

import type { ConversationSource } from "../api";
import { GlobeIcon, PhoneIcon } from "./icons";

export const sourceLabel = (source: ConversationSource) => (source === "twilio" ? "Phone" : "Web");

/** Icon only; the label goes in `title`/`aria-label` (or next to it, see SourceTag). */
export function SourceIcon({ source, size = 15 }: { source: ConversationSource; size?: number }) {
  return (
    <span className={`src-icon src-${source}`} role="img" aria-label={sourceLabel(source)} title={sourceLabel(source)}
      data-source={source}>
      {source === "twilio" ? <PhoneIcon size={size} /> : <GlobeIcon size={size} />}
    </span>
  );
}

/** Icon + "Phone"/"Web", for meta rows. */
export function SourceTag({ source }: { source: ConversationSource }) {
  return (
    <span className={`src-tag src-${source}`} data-source={source}>
      {source === "twilio" ? <PhoneIcon size={12} /> : <GlobeIcon size={12} />}
      {sourceLabel(source)}
    </span>
  );
}

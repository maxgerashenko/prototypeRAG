import type { CallSnapshot } from "../voice/CallEngine";
import { fmtDur } from "../utils/format";
import { CheckBigIcon } from "../components/icons";

interface EndedScreenProps {
  snapshot: CallSnapshot;
  onReadConversation: () => void;
  onBackToConversations: () => void;
}

export function EndedScreen({ snapshot, onReadConversation, onBackToConversations }: EndedScreenProps) {
  const biz = snapshot.biz;
  const msgCount = snapshot.messages.filter((m) => "role" in m && (m.role === "user" || m.role === "assistant")).length;
  const hasUser = snapshot.messages.some((m) => "role" in m && m.role === "user");
  const canRead = !!(snapshot.conversationId && hasUser);

  return (
    <div className="ended-screen">
      <div className="ended-check">
        <CheckBigIcon />
      </div>
      <div className="ended-title-block">
        <div className="ended-title-block__title">Call ended</div>
        <div className="ended-title-block__sub">Saved to {biz ? biz.name : ""}’s conversations</div>
      </div>
      <div className="ended-tiles">
        <div className="ended-tile">
          <div className="ended-tile__value">{fmtDur(snapshot.seconds)}</div>
          <div className="ended-tile__label">Duration</div>
        </div>
        <div className="ended-tile">
          <div className="ended-tile__value">{msgCount}</div>
          <div className="ended-tile__label">Messages</div>
        </div>
      </div>
      <div className="ended-actions">
        <button
          className={"ended-read-btn" + (canRead ? "" : " ended-read-btn--disabled")}
          disabled={!canRead}
          onClick={() => {
            if (canRead) onReadConversation();
          }}
        >
          {canRead ? "Read this conversation" : "Nothing was said"}
        </button>
        <button className="ended-back-btn" onClick={onBackToConversations}>
          Back to conversations
        </button>
      </div>
    </div>
  );
}

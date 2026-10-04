import { MicSmallIcon } from "./icons";

export interface BubbleMessage {
  role: "assistant" | "user";
  text: string;
  time: string;
  latencySec?: string | null;
  latencyTitle?: string | null;
  interrupted?: boolean;
}

function assistantMetaText(m: BubbleMessage): string {
  let t = "Assistant · " + m.time;
  if (m.latencySec != null) t += " · " + m.latencySec + " s";
  if (m.interrupted) t += " · interrupted";
  return t;
}

/** Shared chat-bubble renderer used by both the transcript screen (past messages)
 * and the live call screen. Ported from buildMessageBubble() in web/mic-test.html. */
export function MessageBubble({ message }: { message: BubbleMessage }) {
  if (message.role === "assistant") {
    return (
      <div className="msg-assistant">
        <div className="msg-assistant__row">
          <div className="msg-assistant__avatar">
            <span />
            <span />
            <span />
          </div>
          <div className="msg-assistant__col">
            <div className="msg-assistant__bubble">{message.text}</div>
            <div className="msg-assistant__meta" title={message.latencyTitle ?? undefined}>
              {assistantMetaText(message)}
            </div>
          </div>
        </div>
      </div>
    );
  }
  return (
    <div className="msg-user">
      <div className="msg-user__col">
        <div className="msg-user__bubble">{message.text}</div>
        <div className="msg-user__meta">
          <MicSmallIcon />
          You · {message.time}
        </div>
      </div>
    </div>
  );
}

export function ErrorLine({ text }: { text: string }) {
  return <div className="msg-error">{text}</div>;
}

export function ThinkingBubble() {
  return (
    <div className="thinking-bubble">
      <div className="thinking-bubble__avatar" />
      <div className="thinking-bubble__dots">
        <div className="thinking-bubble__dot" />
        <div className="thinking-bubble__dot" />
        <div className="thinking-bubble__dot" />
      </div>
    </div>
  );
}

export function ListeningBubble() {
  return (
    <div className="listening-bubble">
      <div className="listening-bubble__bars">
        <span />
        <span />
        <span />
      </div>
      Listening…
    </div>
  );
}

import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { postChat, type Source } from "../api";

type Role = "user" | "assistant" | "error";

interface Message {
  id: number;
  role: Role;
  text: string;
  sources?: Source[];
}

function formatSource(src: Source): string {
  const heading = src.section_heading || "(no heading)";
  return `${heading} | score: ${src.score.toFixed(3)} | source: ${src.source}`;
}

function MessageView({ message }: { message: Message }) {
  const { role, text, sources } = message;
  return (
    <div className={`message ${role}`}>
      {text}
      {sources && sources.length > 0 && (
        <details className="sources">
          <summary>Sources ({sources.length})</summary>
          <ul className="source-list">
            {sources.map((src, i) => (
              <li key={`${src.chunk_id}-${i}`} className="source-item">
                {formatSource(src)}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

export function ChatApp() {
  const [businessId, setBusinessId] = useState("");
  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [loading, setLoading] = useState(false);
  const conversationId = useRef<string | null>(null);
  const nextId = useRef(0);
  const messagesEl = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = messagesEl.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  function appendMessage(role: Role, text: string, sources?: Source[]) {
    const id = nextId.current++;
    setMessages((prev) => [...prev, { id, role, text, sources }]);
  }

  async function handleSend() {
    const bid = businessId.trim();
    const q = question.trim();
    // the old page let Enter send a second question while one was in flight; `loading` blocks that
    if (!bid || !q || loading) return;

    appendMessage("user", q);
    setQuestion("");
    setLoading(true);

    try {
      const data = await postChat({ business_id: bid, question: q, conversation_id: conversationId.current });
      conversationId.current = data.conversation_id;
      appendMessage("assistant", data.answer, data.sources);
    } catch (err) {
      appendMessage("error", `Error: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setLoading(false);
    }
  }

  function onKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void handleSend();
    }
  }

  return (
    <div className="container">
      <div className="input-group">
        <label htmlFor="business_id">Business ID (UUID):</label>
        <input
          type="text"
          id="business_id"
          placeholder="Paste UUID here"
          value={businessId}
          onChange={(e) => setBusinessId(e.target.value)}
        />
      </div>
      <div id="messages" ref={messagesEl}>
        {messages.map((m) => (
          <MessageView key={m.id} message={m} />
        ))}
      </div>
      <div className="controls">
        <input
          type="text"
          id="question"
          placeholder="Ask a question..."
          autoComplete="off"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={onKeyDown}
        />
        <button id="send" disabled={loading} onClick={() => void handleSend()}>
          {loading ? "Thinking..." : "Send"}
        </button>
      </div>
    </div>
  );
}

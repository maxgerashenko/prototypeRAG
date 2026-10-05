// Shapes of the backend's JSON — app/api/chat.py, app/api/conversations.py, app/voice/browser_ws.py.

export interface Source {
  chunk_id: string;
  section_heading: string | null;
  score: number;
  source: string;
}

export interface ChatRequest {
  business_id: string;
  question: string;
  conversation_id: string | null;
}

export interface ChatResponse {
  answer: string;
  conversation_id: string;
  sources: Source[];
}

export async function postChat(payload: ChatRequest): Promise<ChatResponse> {
  const res = await fetch("/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}: ${res.statusText}`);
  }
  return res.json() as Promise<ChatResponse>;
}

// JSON text frames on /voice/browser; binary frames are PCM16 audio.
export type VoiceEvent =
  | { type: "transcript"; text: string }
  | { type: "reply"; text: string }
  | { type: "clear" }
  | { type: "latency"; [metric: string]: string | number }
  | { type: "error"; message: string }
  | { type: "conversation_id"; id: string };

// --- voice app (app/api/conversations.py) ---

export interface Business {
  id: string;
  name: string;
  category: string;
  conversation_count: number;
}

export interface ConversationSummary {
  id: string;
  channel: "chat" | "voice";
  started_at: string;
  ended_at: string | null;
  title: string;
  preview: string;
  preview_role: "user" | "assistant" | null;
  message_count: number;
}

export interface StoredMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
}

export interface ConversationDetail extends ConversationSummary {
  messages: StoredMessage[];
}

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}: ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

export const listBusinesses = () => getJson<Business[]>("/businesses");

export const listConversations = (businessId: string) =>
  getJson<ConversationSummary[]>(`/businesses/${encodeURIComponent(businessId)}/conversations`);

export const getConversation = (businessId: string, conversationId: string) =>
  getJson<ConversationDetail>(
    `/businesses/${encodeURIComponent(businessId)}/conversations/${encodeURIComponent(conversationId)}`,
  );

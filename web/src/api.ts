// Shapes of the backend's JSON — app/api/chat.py, app/api/businesses.py, app/api/conversations.py,
// app/voice/browser_ws.py, app/voice/twilio_routes.py.

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
  | { type: "conversation_id"; id: string }
  | { type: "no_speech" } // push-to-talk turn with nothing said (or noise the VAD took for speech)
  | { type: "hangup"; reason: "goodbye" | "silence" }; // the server ends the call (V17); the socket closes next

// --- voice app (app/api/businesses.py, app/api/conversations.py) ---

export interface Location {
  id: string;
  name: string | null;
  url: string | null;
}

export interface Business {
  id: string;
  name: string;
  website: string | null;
  domain: string | null; // website host without "www." (DEC-37)
  conversation_count: number; // conversations with at least one user message
  default_location: Location | null;
}

/** "twilio": came through Twilio (a phone call or a Voice SDK call); "web": the browser voice app or chat page. */
export type ConversationSource = "twilio" | "web";

/** Calls nobody spoke in are not listed. `preview` already starts with "You: " when the caller spoke last. */
export interface ConversationSummary {
  id: string;
  channel: "chat" | "voice";
  source: ConversationSource;
  title: string;
  preview: string;
  started_at: string;
  duration_s: number;
  message_count: number;
}

export interface StoredMessage {
  role: "user" | "assistant";
  content: string;
  at_s: number; // seconds since the conversation started
}

export interface ConversationDetail {
  id: string;
  channel: "chat" | "voice";
  source: ConversationSource;
  title: string;
  started_at: string;
  duration_s: number;
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

// --- Twilio call page (app/voice/twilio_routes.py) ---

export interface TwilioToken {
  token: string;
  identity: string;
}

/** Voice SDK access token. The server explains a refusal in `detail` (not on localhost, not configured). */
export async function fetchToken(): Promise<TwilioToken> {
  const res = await fetch("/twilio/token");
  if (!res.ok) {
    const body = (await res.json().catch(() => ({}))) as { detail?: string };
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  return res.json() as Promise<TwilioToken>;
}

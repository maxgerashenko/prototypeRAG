// Shapes of the backend's JSON — app/api/chat.py, app/api/businesses.py, app/api/conversations.py,
// app/voice/browser_ws.py, app/voice/twilio_routes.py, app/voice/call_feed.py.

export interface Source {
  chunk_id: string;
  section_heading: string | null;
  score: number;
  source: string;
}

/** One message of the prompt sent to the LLM (app/rag/prompt.py's build_prompt). */
export interface PromptMessage {
  role: string;
  content: string;
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
  prompt?: PromptMessage[]; // full prompt, for the debug view
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

// --- live phone calls: GET /voice/feed, Server-Sent Events (app/voice/call_feed.py, DEC-46) ---

export interface LiveCallInfo {
  id: string;
  business_id: string;
  conversation_id: string | null;
  caller: string | null; // the caller's number, or "client:…" for a Voice SDK call
  started_at: number; // ms epoch
}

/** A session event of a phone call, plus `playback` (the bot's audio on the line), stamped
 * with the server time `t` (ms epoch). */
export type LiveEvent = (VoiceEvent | { type: "playback"; active: boolean }) & { t: number };

export type LiveFeedMessage =
  | { type: "snapshot"; calls: (LiveCallInfo & { events: LiveEvent[] })[] }
  | { type: "call_started"; call: LiveCallInfo }
  | { type: "call_event"; call_id: string; event: LiveEvent }
  | { type: "call_ended"; call_id: string; t: number };

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
  phone_numbers: string[]; // dialled numbers that reach this business (businesses.phone_numbers)
}

/** "twilio": came through Twilio (a phone call or a Voice SDK call); "web": the browser voice app or chat page. */
export type ConversationSource = "twilio" | "web";

/** Post-call summary (app/actions/summary.py); all null until the call is summarized (chats never are). */
export type CallOutcome = "booked" | "info_request" | "message" | "transferred" | "unresolved";

export interface CallSummaryFields {
  summary: string | null;
  intent: string | null;
  outcome: CallOutcome | null;
  follow_up: boolean | null;
}

/** Calls nobody spoke in are not listed. `preview` already starts with "You: " when the caller spoke last. */
export interface ConversationSummary extends CallSummaryFields {
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

export interface ConversationDetail extends CallSummaryFields {
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

/** The main Twilio number (app/voice/twilio_routes.py); null when TWILIO_PHONE_NUMBER isn't set. */
export const getMainNumber = () => getJson<{ number: string | null }>("/twilio/number");

/** Point the main number at this business (it leaves any other business), or take it off. */
export async function setMainNumber(businessId: string, assign: boolean): Promise<void> {
  const res = await fetch(`/businesses/${encodeURIComponent(businessId)}/main-number`, {
    method: assign ? "POST" : "DELETE",
  });
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}: ${res.statusText}`);
  }
}

/** One of the account's Twilio numbers and the business holding it, if any (GET /twilio/numbers, #147). */
export interface AccountNumber {
  number: string;
  label: string;
  business_id: string | null;
  business_name: string | null;
}

/** `configured: false` (no Twilio credentials) comes with an empty list and an explaining `message`. */
export const listAccountNumbers = () =>
  getJson<{ configured: boolean; message: string | null; numbers: AccountNumber[] }>("/twilio/numbers");

/**
 * Give the business one of the account's numbers (stage 1: one per business). If another business
 * holds it the server answers 409 with who — ask the owner, then call again with `reassign: true`.
 * Used by the Settings screen (#144); a number of null takes the business's numbers off.
 */
export async function assignNumber(
  businessId: string,
  number: string | null,
  reassign = false,
): Promise<{ conflict: string | null }> {
  const url = `/businesses/${encodeURIComponent(businessId)}/numbers`;
  const res = number === null
    ? await fetch(url, { method: "DELETE" })
    : await fetch(url, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ number, reassign }),
      });
  if (res.status === 409) {
    const body = (await res.json().catch(() => ({}))) as { detail?: string };
    return { conflict: body.detail ?? "number is used by another business" };
  }
  if (!res.ok) {
    const body = (await res.json().catch(() => ({}))) as { detail?: string };
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  return { conflict: null };
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

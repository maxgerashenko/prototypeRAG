// Shapes of the backend's JSON — app/api/chat.py, app/api/businesses.py, app/api/conversations.py,
// app/api/business_settings.py, app/api/sessions.py, app/voice/twilio_numbers.py,
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

export const listBusinesses = () => getJson<Business[]>("/businesses");

export const listConversations = (businessId: string) =>
  getJson<ConversationSummary[]>(`/businesses/${encodeURIComponent(businessId)}/conversations`);

export const getConversation = (businessId: string, conversationId: string) =>
  getJson<ConversationDetail>(
    `/businesses/${encodeURIComponent(businessId)}/conversations/${encodeURIComponent(conversationId)}`,
  );

// --- business settings (app/api/business_settings.py, app/voice/twilio_numbers.py) ---

/** `text` is the owner's part only ("" = only the disclosure); the spoken opening always starts with "Hi, I am an AI assistant." (DEC-53). */
export interface GreetingStatus {
  text: string;
  is_default: boolean;
  full_text?: string; // what is spoken: the disclosure + text
  cached: boolean; // audio exists for both sample rates
  cached_at: string | null; // ISO time
  max_length: number;
  cache_error?: string | null; // PUT only: the text is saved but the TTS failed
}

export interface TopQuestion {
  question: string;
  count: number;
  percent: number;
  answer: string | null;
}

export interface TopQuestions {
  total_questions: number;
  conversations: number;
  days: number;
  questions: TopQuestion[];
}

/** A number of the Twilio account; `business_id`/`business_name` say which business holds it (DEC-52). */
export interface TwilioNumber {
  number: string;
  friendly_name: string;
  voice: boolean;
  sms: boolean;
  routed: boolean; // its Voice URL points at this app
  business_id: string | null;
  business_name: string | null;
}

export interface TwilioNumbers {
  numbers: TwilioNumber[];
  error: string | null;
}

export interface AssignResult {
  business_id: string;
  phone_numbers: string[];
  moved_from: string[];
}

/** Send JSON; a failure carries the server's `detail` when it is a string (e.g. the 422 text). */
async function sendJson<T>(url: string, method: string, body?: unknown): Promise<T> {
  const res = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    const err = (await res.json().catch(() => ({}))) as { detail?: unknown };
    throw new Error(typeof err.detail === "string" ? err.detail : `HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

const bizUrl = (businessId: string) => `/businesses/${encodeURIComponent(businessId)}`;

export const getGreeting = (businessId: string) => getJson<GreetingStatus>(`${bizUrl(businessId)}/greeting`);

export const saveGreeting = (businessId: string, text: string) =>
  sendJson<GreetingStatus>(`${bizUrl(businessId)}/greeting`, "PUT", { text });

export const getTopQuestions = (businessId: string, limit = 5, days = 30) =>
  getJson<TopQuestions>(`${bizUrl(businessId)}/top-questions?limit=${limit}&days=${days}`);

export const listTwilioNumbers = () => getJson<TwilioNumbers>("/twilio/numbers");

/** Give this business the number; any other business loses it (DEC-52). */
export const assignNumber = (businessId: string, number: string) =>
  sendJson<AssignResult>(`${bizUrl(businessId)}/number`, "PUT", { number });

// --- bookings (app/api/bookings.py) ---

export interface BookingItem {
  id: string;
  date: string; // YYYY-MM-DD, business-local
  start_min: number; // minutes since local midnight
  end_min: number;
  name: string;
  service: string | null;
  party_size: number | null;
  phone: string | null;
  notes: string | null;
  by_assistant: boolean; // booked on a call or chat (it has a conversation)
}

export interface SessionAttendee {
  id: string;
  name: string;
  phone: string | null;
  by_assistant: boolean;
  conversation_id: string | null; // the conversation that booked them (null: added by hand)
}

/** A group session (class): one start hour, `max_attendees` seats; `attendees` are its bookings. */
export interface SessionItem {
  id: string;
  business_id: string; // kept even though the demo shows every calendar event to every business
  source: "app" | "google"; // google = imported from Google Calendar: time and name come from there
  date: string; // YYYY-MM-DD, business-local
  start_min: number;
  end_min: number;
  name: string;
  max_attendees: number;
  private: boolean; // closed to the public: the assistant never books into it, only people added by hand
  booked: number;
  attendees: SessionAttendee[];
}

export interface NewSession {
  date: string;
  hour: number;
  name: string;
  max_attendees: number;
  private: boolean;
}

export interface SessionDeleted {
  cancelled_bookings: number;
  texted: number;
  no_phone: number;
}

export interface Bookings {
  timezone: string;
  hours: { open: number; close: number } | null; // hours of the booking rules; null = none set
  bookings: BookingItem[];
  sessions: SessionItem[];
  busy: BusyItem[]; // other businesses' events as anonymous blocks; always [] in the demo (one shared calendar)
}

export interface BusyItem {
  date: string;
  start_min: number;
  end_min: number;
  label: string;
}

/** Confirmed bookings whose local start day is in [start, end] (YYYY-MM-DD, at most 62 days). */
export const getBookings = (businessId: string, start: string, end: string, sync = false) =>
  getJson<Bookings>(`${bizUrl(businessId)}/bookings?start=${start}&end=${end}${sync ? "&sync=1" : ""}`);

export const createSession = (businessId: string, s: NewSession) =>
  sendJson<SessionItem>(`${bizUrl(businessId)}/sessions`, "POST", s);

export const updateSession = (
  businessId: string,
  id: string,
  patch: { name?: string; max_attendees?: number; private?: boolean },
) => sendJson<SessionItem>(`${bizUrl(businessId)}/sessions/${encodeURIComponent(id)}`, "PATCH", patch);

/** Cancels the session and its bookings; the result says how many callers were texted. */
export const deleteSession = (businessId: string, id: string) =>
  sendJson<SessionDeleted>(`${bizUrl(businessId)}/sessions/${encodeURIComponent(id)}`, "DELETE");

export const removeAttendee = (businessId: string, sessionId: string, bookingId: string) =>
  sendJson<SessionItem & { texted: boolean }>(
    `${bizUrl(businessId)}/sessions/${encodeURIComponent(sessionId)}/attendees/${encodeURIComponent(bookingId)}`,
    "DELETE",
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

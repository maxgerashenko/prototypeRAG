// Mock backend for the browser tests: the HTTP API of app/api/*.py and the
// /voice/browser WebSocket of app/voice/browser_ws.py, driven from the test.

import type { Page, Request, Route, WebSocketRoute } from "@playwright/test";
import type {
  Business, BookingItem, SessionItem, ConversationDetail, ConversationSource, ConversationSummary, GreetingStatus, TopQuestions, TwilioNumbers, VoiceEvent,
} from "../src/api";

const MIN = 60_000;

export function biz(id: string, name: string, website: string | null, conversation_count = 0, domain: string | null = null): Business {
  return { id, name, website, domain, conversation_count, default_location: null, phone_numbers: [] };
}

/** A stored conversation, shaped like app/api/conversations.py: messages alternate
 * assistant/user starting with the greeting, 19 s apart (at_s 2, 21, 40, …). */
export function convo(
  id: string, title: string, lines: string[], startedMsAgo = 60 * MIN, durS = 95, now = Date.now(),
  source: ConversationSource = "web",
): ConversationDetail {
  const messages = lines.map((content, i) => ({
    role: (i % 2 === 0 ? "assistant" : "user") as "assistant" | "user",
    content,
    at_s: 2 + i * 19,
  }));
  return { id, channel: "voice", source, title, started_at: new Date(now - startedMsAgo).toISOString(), duration_s: durS, messages,
    summary: null, intent: null, outcome: null, follow_up: null };
}

/** List row for a conversation: preview = last message, "You: " first if the caller said it. */
export function summary(c: ConversationDetail): ConversationSummary {
  const last = c.messages[c.messages.length - 1];
  const preview = last ? (last.role === "user" ? "You: " : "") + last.content : "";
  const { messages, ...rest } = c;
  return { ...rest, preview, message_count: messages.length };
}

/** A confirmed booking, shaped like app/api/bookings.py (60 min at 10:00 on 2026-11-05 unless overridden). */
export function booking(over: Partial<BookingItem> = {}): BookingItem {
  return {
    id: "bk1", date: "2026-11-05", start_min: 600, end_min: 660, name: "Ann Lee", service: "Haircut",
    party_size: null, phone: null, notes: null, by_assistant: false, ...over,
  };
}

/** A group session at 14:00 on 2026-11-05 (one hour, 8 seats, nobody booked) unless overridden. */
export function session(over: Partial<SessionItem> = {}): SessionItem {
  return {
    id: "s1", date: "2026-11-05", start_min: 840, end_min: 900, name: "Yoga flow", max_attendees: 8, private: false, booked: 0, attendees: [], ...over,
  };
}

/** A session with these attendees; `booked` follows the list. */
export function sessionWith(attendees: SessionItem["attendees"], over: Partial<SessionItem> = {}): SessionItem {
  return session({ attendees, booked: attendees.length, ...over });
}

export interface ApiState {
  businesses: Business[];
  conversations: Record<string, ConversationDetail[]>;
  /** Make an endpoint fail: "businesses" | "conversations" | "detail" | "greeting" | "greeting-save" |
   * "questions" | "numbers" | "assign" | "bookings". */
  fail: Set<string>;
  /** Hold an endpoint's answer for this many ms (same keys as `fail`). */
  delay: Record<string, number>;
  /** Business settings (app/api/business_settings.py): one greeting for every business. */
  greeting: GreetingStatus;
  questions: TopQuestions;
  /** GET /twilio/numbers; assigning moves `business_id`/`business_name` like PUT /businesses/{id}/number. */
  numbers: TwilioNumbers;
  requests: string[];
  /** GET /businesses/{id}/bookings: every request as path + query. */
  bookingRequests: string[];
  /** Confirmed bookings of every business; the route returns those inside ?start&end. */
  bookings: BookingItem[];
  /** Group sessions of every business; GET bookings returns those inside ?start&end. */
  sessions: SessionItem[];
  /** Session writes seen: "POST|PATCH|DELETE path" with the JSON body (if any). */
  sessionRequests: { method: string; path: string; body: unknown }[];
  /** Answer the next POST /sessions with this status and a {detail}; cleared after use. */
  sessionPostFail: { status: number; detail: string } | null;
  /** Opening hours of the booking rules; null = none set. */
  bookingHours: { open: number; close: number } | null;
  /** GET /twilio/number; null = TWILIO_PHONE_NUMBER not set. */
  mainNumber: string | null;
}

export async function mockApi(page: Page, init: Partial<ApiState> = {}): Promise<ApiState> {
  const api: ApiState = {
    businesses: [], conversations: {}, fail: new Set(), delay: {}, requests: [], bookingRequests: [], bookings: [], sessions: [], sessionRequests: [], sessionPostFail: null, bookingHours: null, mainNumber: null,
    greeting: { text: "", is_default: true, cached: false, cached_at: null, max_length: 300 },
    questions: { total_questions: 0, conversations: 0, days: 30, questions: [] },
    numbers: { numbers: [], error: null },
    ...init,
  };
  // answer (or fail) a settings endpoint, after its optional delay
  const reply = async (route: Route, key: string, body: unknown, status = 200) => {
    if (api.delay[key]) await new Promise((r) => setTimeout(r, api.delay[key]));
    if (api.fail.has(key)) return route.fulfill({ status: 500, body: "boom" });
    return route.fulfill({ status, ...json(body) });
  };
  await page.route("**/twilio/numbers", (route) => {
    api.requests.push("GET /twilio/numbers");
    return reply(route, "numbers", api.numbers);
  });
  const json = (body: unknown) => ({ contentType: "application/json", body: JSON.stringify(body) });
  await page.route("**/twilio/number", (route) => route.fulfill(json({ number: api.mainNumber })));
  await page.route(
    (url) => url.pathname.startsWith("/businesses"),
    async (route) => {
      const path = new URL(route.request().url()).pathname;
      const method = route.request().method();
      api.requests.push(method === "GET" ? path : `${method} ${path}`);
      const [, , bizId, sub, convoId] = path.split("/");
      if (sub === "main-number" && api.mainNumber) {
        // like app/api/businesses.py: the number moves to this business, or comes off it
        const n = api.mainNumber;
        api.businesses = api.businesses.map((b) => {
          const rest = b.phone_numbers.filter((x) => x !== n);
          return { ...b, phone_numbers: method === "POST" && b.id === bizId ? [...rest, n] : rest };
        });
        return route.fulfill(json({ business_id: bizId, number: method === "POST" ? n : null }));
      }
      if (sub === "greeting") {
        if (method === "GET") return reply(route, "greeting", api.greeting);
        const text = (route.request().postDataJSON() as { text: string }).text;
        if (text.length > api.greeting.max_length) {
          return route.fulfill({ status: 422, ...json({ detail: `Greeting is too long (max ${api.greeting.max_length} characters).` }) });
        }
        if (!api.fail.has("greeting-save")) {
          api.greeting = { ...api.greeting, text, is_default: text === "", cached: true, cached_at: new Date().toISOString() };
        }
        return reply(route, "greeting-save", api.greeting);
      }
      if (sub === "sessions") {
        // like app/api/sessions.py; PATCH/DELETE/attendee-DELETE act on api.sessions
        const [, , , , sid, , bid] = path.split("/");
        const body = method === "POST" || method === "PATCH" ? route.request().postDataJSON() : undefined;
        api.sessionRequests.push({ method, path, body });
        const fix = (x: SessionItem) => ({ ...x, booked: x.attendees.length });
        if (method === "POST") {
          if (api.sessionPostFail) {
            const f = api.sessionPostFail;
            api.sessionPostFail = null;
            return route.fulfill({ status: f.status, ...json({ detail: f.detail }) });
          }
          const b = body as { date: string; hour: number; name: string; max_attendees: number; private?: boolean };
          const made = session({ id: `s${api.sessions.length + 100}`, date: b.date, start_min: b.hour * 60, end_min: b.hour * 60 + 60,
            name: b.name, max_attendees: b.max_attendees, private: !!b.private });
          api.sessions.push(made);
          return route.fulfill(json(made));
        }
        const found = api.sessions.find((x) => x.id === sid);
        if (!found) return route.fulfill({ status: 404, ...json({ detail: "session not found" }) });
        if (method === "PATCH") {
          Object.assign(found, body);
          return route.fulfill(json(fix(found)));
        }
        if (bid) {
          const gone = found.attendees.find((a) => a.id === bid);
          found.attendees = found.attendees.filter((a) => a.id !== bid);
          return route.fulfill(json({ ...fix(found), texted: !!gone?.phone }));
        }
        api.sessions = api.sessions.filter((x) => x.id !== sid);
        const phones = found.attendees.filter((a) => a.phone).length;
        return route.fulfill(json({ cancelled_bookings: found.attendees.length, texted: phones, no_phone: found.attendees.length - phones }));
      }
      if (sub === "bookings") {
        const q = new URL(route.request().url());
        api.bookingRequests.push(q.pathname + q.search);
        const start = q.searchParams.get("start") ?? "", end = q.searchParams.get("end") ?? "";
        return reply(route, "bookings", {
          timezone: "America/New_York", hours: api.bookingHours,
          bookings: api.bookings.filter((b) => b.date >= start && b.date <= end),
          sessions: api.sessions.filter((x) => x.date >= start && x.date <= end).map((x) => ({ ...x, booked: x.attendees.length })),
        });
      }
      if (sub === "top-questions") return reply(route, "questions", api.questions);
      if (sub === "number") {
        const number = (route.request().postDataJSON() as { number: string }).number;
        const movedFrom = api.businesses.filter((b) => b.id !== bizId && b.phone_numbers.includes(number)).map((b) => b.id);
        if (!api.fail.has("assign")) {
          api.businesses = api.businesses.map((b) => (
            b.id === bizId ? { ...b, phone_numbers: [number] } : { ...b, phone_numbers: b.phone_numbers.filter((x) => x !== number) }
          ));
          const name = api.businesses.find((b) => b.id === bizId)?.name ?? null;
          api.numbers = { ...api.numbers, numbers: api.numbers.numbers.map((n) => (
            n.number === number ? { ...n, business_id: bizId!, business_name: name } : n.business_id === bizId ? { ...n, business_id: null, business_name: null } : n
          )) };
        }
        return reply(route, "assign", { business_id: bizId, phone_numbers: [number], moved_from: movedFrom });
      }
      const kind = convoId ? "detail" : bizId ? "conversations" : "businesses";
      if (api.fail.has(kind)) return route.fulfill({ status: 500, body: "boom" });
      if (kind === "businesses") return route.fulfill(json(api.businesses));
      const list = api.conversations[bizId] ?? [];
      if (kind === "conversations") return route.fulfill(json(list.map(summary)));
      const found = list.find((c) => c.id === convoId);
      return found ? route.fulfill(json(found)) : route.fulfill({ status: 404, body: '{"detail":"conversation not found"}' });
    },
  );
  return api;
}

export interface VoiceOptions {
  conversationId?: string;
  /** Greeting sent right after conversation_id; null for none. */
  greeting?: string | null;
  /** false: don't confirm the call — the test scripts the opening itself. */
  accept?: boolean;
}

export interface VoiceServer {
  /** Change how the next call opens (call before the page connects). */
  configure: (opts: VoiceOptions) => void;
  /** Resolves with the socket once the page connects. */
  connected: Promise<WebSocketRoute>;
  url: () => string;
  frames: Buffer[];
  /** JSON text frames from the page (ptt_start / ptt_end). */
  texts: Record<string, unknown>[];
  send: (ev: VoiceEvent) => void;
  sendAudio: (samples: number, value?: number) => void;
  close: (code?: number, reason?: string) => Promise<void>;
  closedByClient: () => boolean;
}

/**
 * Scripted /voice/browser. By default it confirms the call (conversation_id) and greets,
 * like CallSession.start(). Must be set up before page.goto(): Playwright routes
 * WebSockets through a script injected at page load.
 */
export async function mockVoice(page: Page, init: VoiceOptions = {}): Promise<VoiceServer> {
  let opts = { ...init };
  let socket: WebSocketRoute | undefined;
  let wsUrl = "";
  let clientClosed = false;
  const frames: Buffer[] = [];
  const texts: Record<string, unknown>[] = [];
  let resolve!: (ws: WebSocketRoute) => void;
  const connected = new Promise<WebSocketRoute>((r) => (resolve = r));
  await page.routeWebSocket(/\/voice\/browser/, (ws) => {
    socket = ws;
    wsUrl = ws.url();
    ws.onMessage((m) => {
      if (typeof m === "string") texts.push(JSON.parse(m));
      else frames.push(m);
    });
    ws.onClose((code, reason) => {
      clientClosed = true;
      void ws.close({ code, reason }); // with an onClose handler Playwright leaves completing the close to us
    });
    if (opts.accept !== false) {
      ws.send(JSON.stringify({ type: "conversation_id", id: opts.conversationId ?? "new-call" }));
      const greeting = opts.greeting === undefined ? "Hi, you've reached the business. How can I help?" : opts.greeting;
      if (greeting) ws.send(JSON.stringify({ type: "reply", text: greeting }));
    }
    resolve(ws);
  });
  return {
    configure: (o) => {
      opts = { ...opts, ...o };
    },
    connected,
    url: () => wsUrl,
    frames,
    texts,
    send: (ev) => socket!.send(JSON.stringify(ev)),
    sendAudio: (samples, value = 1000) => {
      const pcm = new Int16Array(samples).fill(value);
      socket!.send(Buffer.from(pcm.buffer));
    },
    close: (code = 1000, reason = "") => socket!.close({ code, reason }),
    closedByClient: () => clientClosed,
  };
}

/** True if a PCM16 frame carries any sound. */
export const hasSound = (frame: Buffer) => {
  const pcm = new Int16Array(frame.buffer, frame.byteOffset, frame.byteLength / 2);
  return pcm.some((s) => s !== 0);
};

export const pathOf = (r: Request) => new URL(r.url()).pathname;

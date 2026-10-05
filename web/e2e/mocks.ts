// Mock backend for the browser tests: the HTTP API of app/api/*.py and the
// /voice/browser WebSocket of app/voice/browser_ws.py, driven from the test.

import type { Page, Request, WebSocketRoute } from "@playwright/test";
import type { Business, ConversationDetail, ConversationSummary, VoiceEvent } from "../src/api";

const MIN = 60_000;

export function biz(id: string, name: string, website: string | null, conversation_count = 0, domain: string | null = null): Business {
  return { id, name, website, domain, conversation_count, default_location: null };
}

/** A stored conversation, shaped like app/api/conversations.py: messages alternate
 * assistant/user starting with the greeting, 19 s apart (at_s 2, 21, 40, …). */
export function convo(
  id: string, title: string, lines: string[], startedMsAgo = 60 * MIN, durS = 95, now = Date.now(),
): ConversationDetail {
  const messages = lines.map((content, i) => ({
    role: (i % 2 === 0 ? "assistant" : "user") as "assistant" | "user",
    content,
    at_s: 2 + i * 19,
  }));
  return { id, channel: "voice", title, started_at: new Date(now - startedMsAgo).toISOString(), duration_s: durS, messages };
}

/** List row for a conversation: preview = last message, "You: " first if the caller said it. */
export function summary(c: ConversationDetail): ConversationSummary {
  const last = c.messages[c.messages.length - 1];
  const preview = last ? (last.role === "user" ? "You: " : "") + last.content : "";
  const { messages, ...rest } = c;
  return { ...rest, preview, message_count: messages.length };
}

export interface ApiState {
  businesses: Business[];
  conversations: Record<string, ConversationDetail[]>;
  /** Make an endpoint fail: "businesses" | "conversations" | "detail". */
  fail: Set<string>;
  requests: string[];
}

export async function mockApi(page: Page, init: Partial<ApiState> = {}): Promise<ApiState> {
  const api: ApiState = { businesses: [], conversations: {}, fail: new Set(), requests: [], ...init };
  const json = (body: unknown) => ({ contentType: "application/json", body: JSON.stringify(body) });
  await page.route(
    (url) => url.pathname.startsWith("/businesses"),
    async (route) => {
      const path = new URL(route.request().url()).pathname;
      api.requests.push(path);
      const [, , bizId, , convoId] = path.split("/");
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

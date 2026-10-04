// WebSocket protocol for /voice/browser, matching app/voice/browser_ws.py exactly.
//
// Client -> server: binary PCM16 16 kHz mono frames (320 samples each) while talking,
// plus JSON text frames {"type": "ptt_start"} / {"type": "ptt_end"}.
//
// Server -> client: binary PCM16 audio to play, plus JSON text frames (ServerEvent).
// Close code 4404 means "unknown business".

export interface ConversationIdEvent {
  type: "conversation_id";
  id: string;
}
export interface ReplyEvent {
  type: "reply";
  text: string;
}
export interface TranscriptEvent {
  type: "transcript";
  text: string;
}
export interface LatencyEvent {
  type: "latency";
  stt_ms?: number;
  llm_first_sentence_ms?: number;
  tts_first_audio_ms?: number;
  total_ms?: number;
  [key: string]: unknown;
}
export interface ClearEvent {
  type: "clear";
}
export interface NoSpeechEvent {
  type: "no_speech";
}
export interface ErrorEvent {
  type: "error";
  message: string;
}

export type ServerEvent =
  | ConversationIdEvent
  | ReplyEvent
  | TranscriptEvent
  | LatencyEvent
  | ClearEvent
  | NoSpeechEvent
  | ErrorEvent;

export function parseServerEvent(raw: string): ServerEvent | null {
  try {
    const data = JSON.parse(raw);
    if (data && typeof data === "object" && typeof data.type === "string") {
      return data as ServerEvent;
    }
  } catch {
    // ignore malformed frames, same as the original page
  }
  return null;
}

export const UNKNOWN_BUSINESS_CLOSE_CODE = 4404;

export function voiceSocketUrl(businessId: string, mode: "vad" | "ptt", continueFrom: string | null): string {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const params = new URLSearchParams({ business_id: businessId, mode });
  if (continueFrom) params.set("continue_from", continueFrom);
  return `${proto}://${location.host}/voice/browser?${params.toString()}`;
}

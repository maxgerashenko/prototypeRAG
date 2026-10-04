// Call lifecycle: mic capture, WebSocket to /voice/browser, playback, push-to-talk.
// Framework-agnostic on purpose (plain class, not a hook) so pointer-capture / audio
// setup never gets tangled with React re-renders -- a React hook (useCallEngine)
// just subscribes to snapshots. Ported from the `call` object + free functions in
// web/mic-test.html (startCallFor/beginConnect/handleCallEvent/pttDown/pttUp/endCall/…).
import type { BusinessOut } from "../api/types";
import { createMicWorkletNode } from "./audioWorklet";
import { AudioPlayer } from "./playback";
import { parseServerEvent, voiceSocketUrl, type ServerEvent } from "./protocol";
import { fmtDur } from "../utils/format";

export interface AssistantCallMessage {
  role: "assistant";
  text: string;
  time: string;
  latencySec: string | null;
  latencyTitle: string | null;
  interrupted: boolean;
}
export interface UserCallMessage {
  role: "user";
  text: string;
  time: string;
}
export interface ErrorCallMessage {
  kind: "error";
  text: string;
}
export type CallMessage = AssistantCallMessage | UserCallMessage | ErrorCallMessage;

export type BotState = "thinking" | "speaking" | null;

export interface CallSnapshot {
  bizId: string;
  biz: BusinessOut | null;
  talking: boolean;
  botState: BotState;
  messages: CallMessage[];
  conversationId: string | null;
  seconds: number;
  noSpeechActive: boolean;
  userSentAny: boolean;
  ended: boolean;
}

export interface CallEngineCallbacks {
  onUpdate: (snapshot: CallSnapshot) => void;
  /** WebSocket open -- the call screen should show now. */
  onConnected: () => void;
  /** User cancelled while still connecting -- back to select, no error shown. */
  onCancelled: () => void;
  /** Mic/audio/socket setup failed while connecting -- back to select, with a message. */
  onConnectFailed: (message: string) => void;
  /** Server closed with 4404 after the call was live. */
  onUnknownBusiness: () => void;
  /** Call ended normally (hang up, or any other server-side close). */
  onEnded: (snapshot: CallSnapshot) => void;
}

export class CallEngine {
  private ws: WebSocket | null = null;
  private audioCtx: AudioContext | null = null;
  private workletNode: AudioWorkletNode | null = null;
  private micStream: MediaStream | null = null;
  private micSource: MediaStreamAudioSourceNode | null = null;
  private player: AudioPlayer | null = null;

  private talking = false;
  private botState: BotState = null;
  private messages: CallMessage[] = [];
  private buildingAssistant: AssistantCallMessage | null = null;
  private conversationId: string | null = null;

  private seconds = 0;
  private startedAtMs = 0;
  private timerInterval: ReturnType<typeof setInterval> | null = null;
  private noSpeechActive = false;
  private noSpeechTimer: ReturnType<typeof setTimeout> | null = null;

  private userSentAny = false;
  private ended = false;
  /** False until the WebSocket has opened -- distinguishes "still connecting" (any
   * failure is a connect failure) from "was live" (a later close is a normal end). */
  private connected = false;

  constructor(
    public readonly bizId: string,
    public readonly biz: BusinessOut | null,
    private readonly continueFrom: string | null,
    private readonly callbacks: CallEngineCallbacks,
  ) {}

  snapshot(): CallSnapshot {
    return {
      bizId: this.bizId,
      biz: this.biz,
      talking: this.talking,
      botState: this.botState,
      messages: [...this.messages],
      conversationId: this.conversationId,
      seconds: this.seconds,
      noSpeechActive: this.noSpeechActive,
      userSentAny: this.userSentAny,
      ended: this.ended,
    };
  }

  private emitUpdate(): void {
    if (this.ended) return;
    this.callbacks.onUpdate(this.snapshot());
  }

  /** Begin connecting: AudioContext -> mic -> worklet -> WebSocket. Any failure calls
   * onConnectFailed and tears everything down; never throws. */
  async start(): Promise<void> {
    try {
      this.audioCtx = new AudioContext({ sampleRate: 16000 });
    } catch {
      this.failConnect("Audio unavailable");
      return;
    }

    try {
      this.micStream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
    } catch {
      this.failConnect("Microphone blocked");
      return;
    }
    if (this.ended) return;

    try {
      this.workletNode = await createMicWorkletNode(this.audioCtx);
    } catch {
      this.failConnect("Audio setup failed");
      return;
    }
    if (this.ended) return;

    this.player = new AudioPlayer(this.audioCtx);
    this.micSource = this.audioCtx.createMediaStreamSource(this.micStream);
    this.micSource.connect(this.workletNode);
    this.workletNode.port.onmessage = (e: MessageEvent) => {
      const data = e.data as { data?: ArrayBuffer } | undefined;
      if (data?.data && this.ws && this.ws.readyState === WebSocket.OPEN && this.talking) {
        this.ws.send(data.data);
      }
    };

    const ws = new WebSocket(voiceSocketUrl(this.bizId, "ptt", this.continueFrom));
    ws.binaryType = "arraybuffer";
    this.ws = ws;

    ws.addEventListener("open", () => {
      if (this.ended) return;
      this.connected = true;
      this.startedAtMs = Date.now();
      this.callbacks.onConnected();
      this.timerInterval = setInterval(() => {
        this.seconds = Math.floor((Date.now() - this.startedAtMs) / 1000);
        this.emitUpdate();
      }, 500);
    });

    ws.addEventListener("message", (event: MessageEvent) => {
      if (this.ended) return;
      if (typeof event.data === "string") {
        const parsed = parseServerEvent(event.data);
        if (parsed) this.handleEvent(parsed);
      } else {
        this.handleAudio(event.data as ArrayBuffer);
      }
    });

    ws.addEventListener("close", (event: CloseEvent) => {
      if (this.ended) return;
      if (!this.connected) {
        this.failConnect(event.code === 4404 ? "Unknown business" : "Connection failed");
        return;
      }
      if (event.code === 4404) {
        this.ended = true;
        this.teardownAudio();
        this.callbacks.onUnknownBusiness();
        return;
      }
      this.ended = true;
      this.teardownAudio();
      this.callbacks.onEnded(this.snapshot());
    });
    ws.addEventListener("error", () => {});
  }

  private failConnect(message: string): void {
    if (this.ended) return;
    this.ended = true;
    try {
      this.ws?.close(1000);
    } catch {
      // already closed
    }
    this.teardownAudio();
    this.callbacks.onConnectFailed(message);
  }

  cancelConnecting(): void {
    if (this.ended) return;
    this.ended = true;
    try {
      this.ws?.close(1000);
    } catch {
      // already closed
    }
    this.teardownAudio();
    this.callbacks.onCancelled();
  }

  private handleEvent(data: ServerEvent): void {
    switch (data.type) {
      case "conversation_id":
        this.conversationId = data.id;
        break;
      case "reply":
        this.appendAssistantText(data.text);
        break;
      case "transcript":
        this.closeCurrentAssistantMsg(false);
        this.messages.push({ role: "user", text: data.text, time: fmtDur(this.seconds) });
        this.userSentAny = true;
        break;
      case "latency":
        this.applyLatency(data);
        break;
      case "clear":
        this.player?.stopAll();
        this.closeCurrentAssistantMsg(true);
        break;
      case "no_speech":
        this.botState = null;
        this.showNoSpeech();
        break;
      case "error":
        this.botState = null;
        this.messages.push({ kind: "error", text: data.message });
        break;
      default:
        break;
    }
    this.emitUpdate();
  }

  private appendAssistantText(text: string): void {
    if (this.buildingAssistant) {
      this.buildingAssistant.text = this.buildingAssistant.text ? this.buildingAssistant.text + " " + text : text;
    } else {
      const msg: AssistantCallMessage = {
        role: "assistant",
        text,
        time: fmtDur(this.seconds),
        latencySec: null,
        latencyTitle: null,
        interrupted: false,
      };
      this.messages.push(msg);
      this.buildingAssistant = msg;
    }
  }

  private closeCurrentAssistantMsg(interrupted: boolean): void {
    if (this.buildingAssistant) {
      if (interrupted) this.buildingAssistant.interrupted = true;
      this.buildingAssistant = null;
    }
  }

  private findLastAssistant(): AssistantCallMessage | null {
    for (let i = this.messages.length - 1; i >= 0; i--) {
      const m = this.messages[i];
      if ("role" in m && m.role === "assistant") return m;
    }
    return null;
  }

  private applyLatency(data: Record<string, unknown>): void {
    const msg = this.buildingAssistant ?? this.findLastAssistant();
    if (!msg) return;
    const ttsFirstAudioMs = data.tts_first_audio_ms;
    if (typeof ttsFirstAudioMs === "number") msg.latencySec = (ttsFirstAudioMs / 1000).toFixed(1);
    msg.latencyTitle = Object.entries(data)
      .filter(([k]) => k !== "type")
      .map(([k, v]) => `${k}: ${v}`)
      .join("\n");
  }

  private showNoSpeech(): void {
    if (this.noSpeechTimer) clearTimeout(this.noSpeechTimer);
    this.noSpeechActive = true;
    this.noSpeechTimer = setTimeout(() => {
      this.noSpeechActive = false;
      this.emitUpdate();
    }, 2500);
  }

  private handleAudio(buf: ArrayBuffer): void {
    if (this.talking) return;
    this.player?.schedule(buf, () => {
      if (this.botState === "speaking") {
        this.botState = null;
        this.emitUpdate();
      }
    });
    this.botState = "speaking";
    this.emitUpdate();
  }

  pttDown(): void {
    if (!this.connected || this.ended || this.talking) return;
    this.player?.stopAll();
    this.talking = true;
    this.botState = null;
    if (this.noSpeechTimer) clearTimeout(this.noSpeechTimer);
    this.noSpeechActive = false;
    if (this.ws && this.ws.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify({ type: "ptt_start" }));
    this.emitUpdate();
  }

  pttUp(): void {
    if (!this.talking) return;
    this.talking = false;
    this.botState = "thinking";
    if (this.ws && this.ws.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify({ type: "ptt_end" }));
    this.emitUpdate();
  }

  end(): void {
    if (!this.connected || this.ended) return;
    this.ended = true;
    try {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) this.ws.close(1000);
    } catch {
      // already closed
    }
    const snap = this.snapshot();
    this.teardownAudio();
    this.callbacks.onEnded(snap);
  }

  private teardownAudio(): void {
    this.player?.stopAll();
    if (this.workletNode) {
      try {
        this.workletNode.port.postMessage({ type: "stop" });
        this.workletNode.disconnect();
      } catch {
        // already disconnected
      }
    }
    if (this.micSource) {
      try {
        this.micSource.disconnect();
      } catch {
        // already disconnected
      }
    }
    this.micStream?.getTracks().forEach((t) => t.stop());
    if (this.audioCtx) {
      try {
        void this.audioCtx.close();
      } catch {
        // already closed
      }
    }
    if (this.timerInterval) clearInterval(this.timerInterval);
    if (this.noSpeechTimer) clearTimeout(this.noSpeechTimer);
  }
}

// Mode C call over WebSocket /voice/browser (plan/03-voice-channel.md): mic -> PCM16 16 kHz
// frames up, PCM16 audio + JSON events down. Plain class, not React state: the audio graph,
// socket and playback queue change per frame and must not trigger renders.
//
// Used by the mic test (always transmitting, server VAD decides turns) and by the voice app
// (hold-to-talk, DEC-38): with setTalking(false) the mic frames are replaced by silence, so
// the server's VAD still sees the speech end ~0.5 s after the button is released.

import type { VoiceEvent } from "../api";

export const RATE = 16000;
const FRAME = RATE / 50; // 20 ms per message, same as Twilio frames
const SILENCE = new ArrayBuffer(FRAME * 2);

export type LineKind = "user" | "assistant" | "meta";

// runs on the audio thread: Float32 mic blocks (128 samples) -> 20 ms Int16 frames
const workletSrc = `
  class Capture extends AudioWorkletProcessor {
    constructor() { super(); this.buf = new Int16Array(${FRAME}); this.n = 0; }
    process(inputs) {
      const ch = inputs[0][0];
      if (!ch) return true;
      for (let i = 0; i < ch.length; i++) {
        const s = Math.max(-1, Math.min(1, ch[i]));
        this.buf[this.n++] = s < 0 ? s * 0x8000 : s * 0x7fff;
        if (this.n === this.buf.length) { this.port.postMessage(this.buf.buffer.slice(0)); this.n = 0; }
      }
      return true;
    }
  }
  registerProcessor('capture', Capture);`;

export interface VoiceCallHandlers {
  /** Mic-test style log lines for every event. */
  onLine?: (kind: LineKind, text: string) => void;
  /** Every JSON event from the server, after the built-in handling (clear stops playback). */
  onEvent?: (ev: VoiceEvent) => void;
  /** Bot audio started (true) or the playback queue ran empty / was cleared (false). */
  onPlayback?: (active: boolean) => void;
  /** The socket closed; byServer = not closed by stop(). Fires before onEnded. */
  onClose?: (code: number, reason: string, byServer: boolean) => void;
  /** stop() ran (hang-up, socket closed, or start failed). Can fire more than once. */
  onEnded: () => void;
}

export interface VoiceCallOptions {
  /** Start with the mic transmitting (mic test) or muted until setTalking(true). */
  transmitting?: boolean;
}

export class VoiceCall {
  private ws: WebSocket | null = null;
  private ctx: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private nextPlay = 0;
  private playing: AudioBufferSourceNode[] = [];
  private ended = false; // set by stop(); start() checks it after each await (hang up during the mic prompt)
  private transmitting: boolean;
  private dropAudio = false; // while the user holds to talk, bot audio is not played

  constructor(
    private readonly handlers: VoiceCallHandlers,
    options: VoiceCallOptions = {},
  ) {
    this.transmitting = options.transmitting ?? true;
  }

  async start(businessId: string): Promise<void> {
    const ctx = new AudioContext({ sampleRate: RATE }); // the browser resamples the mic to 16 kHz
    this.ctx = ctx;
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    if (this.ended) {
      stream.getTracks().forEach((t) => t.stop());
      return;
    }
    this.stream = stream;
    const url = URL.createObjectURL(new Blob([workletSrc], { type: "application/javascript" }));
    try {
      await ctx.audioWorklet.addModule(url);
    } finally {
      URL.revokeObjectURL(url);
    }
    if (this.ended) return;
    const capture = new AudioWorkletNode(ctx, "capture");
    ctx.createMediaStreamSource(this.stream).connect(capture);

    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/voice/browser?business_id=${encodeURIComponent(businessId)}`);
    ws.binaryType = "arraybuffer";
    ws.onmessage = (m: MessageEvent<string | ArrayBuffer>) =>
      typeof m.data === "string" ? this.onEvent(JSON.parse(m.data) as VoiceEvent) : this.play(m.data);
    ws.onclose = (e) => {
      this.handlers.onLine?.("meta", `[closed ${e.code}${e.reason ? " " + e.reason : ""}]`);
      this.handlers.onClose?.(e.code, e.reason, this.ws === ws);
      this.stop();
    };
    this.ws = ws;
    capture.port.onmessage = (m: MessageEvent<ArrayBuffer>) => {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) this.ws.send(this.transmitting ? m.data : SILENCE);
    };
  }

  /** Hold-to-talk: mic audio goes out only while talking; pressing also silences the bot. */
  setTalking(on: boolean): void {
    this.transmitting = on;
    this.dropAudio = on;
    if (on) this.clearPlayback();
  }

  // Idempotent: called by the user (hang up), by ws.onclose, and on start() failure.
  stop(): void {
    this.ended = true;
    if (this.ws) {
      const w = this.ws;
      this.ws = null;
      w.close();
    }
    if (this.stream) {
      this.stream.getTracks().forEach((t) => t.stop());
      this.stream = null;
    }
    if (this.ctx) {
      void this.ctx.close();
      this.ctx = null;
    }
    this.playing = [];
    this.nextPlay = 0;
    this.handlers.onEnded();
  }

  private onEvent(ev: VoiceEvent): void {
    const { onLine } = this.handlers;
    switch (ev.type) {
      case "transcript":
        onLine?.("user", ev.text);
        break;
      case "reply":
        onLine?.("assistant", ev.text);
        break;
      case "clear":
        this.clearPlayback();
        onLine?.("meta", "[barge-in: playback cleared]");
        break;
      case "latency": {
        const { type: _type, ...t } = ev;
        onLine?.("meta", Object.entries(t).map(([k, v]) => `${k}=${v}`).join("  "));
        break;
      }
      case "error":
        onLine?.("meta", "Error: " + ev.message);
        break;
      case "conversation_id":
        onLine?.("meta", "conversation " + ev.id);
        break;
    }
    this.handlers.onEvent?.(ev);
  }

  private play(arrayBuffer: ArrayBuffer): void {
    const ctx = this.ctx;
    const pcm = new Int16Array(arrayBuffer);
    if (!ctx || !pcm.length || this.dropAudio) return;
    const buffer = ctx.createBuffer(1, pcm.length, RATE);
    const data = buffer.getChannelData(0);
    for (let i = 0; i < pcm.length; i++) data[i] = pcm[i] / 0x8000;
    const src = ctx.createBufferSource();
    src.buffer = buffer;
    src.connect(ctx.destination);
    this.nextPlay = Math.max(this.nextPlay, ctx.currentTime);
    src.start(this.nextPlay);
    this.nextPlay += buffer.duration;
    if (!this.playing.length) this.handlers.onPlayback?.(true);
    this.playing.push(src);
    src.onended = () => {
      const before = this.playing.length;
      this.playing = this.playing.filter((s) => s !== src);
      if (before && !this.playing.length) this.handlers.onPlayback?.(false);
    };
  }

  private clearPlayback(): void {
    const had = this.playing.length > 0;
    this.playing.forEach((s) => {
      try {
        s.stop();
      } catch {
        // already stopped
      }
    });
    this.playing = [];
    this.nextPlay = 0;
    if (had) this.handlers.onPlayback?.(false);
  }
}

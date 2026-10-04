// Mic capture worklet, ported verbatim from web/mic-test.html. Runs in
// AudioWorkletGlobalScope, which can't import ES modules, so the Float32->Int16
// encode logic here is a self-contained copy of pcm.ts's clampAndEncodeSample() --
// keep the two in sync if either changes (noted there too).

export const SAMPLE_RATE = 16000;
export const FRAME_SAMPLES = 320; // 20 ms at 16 kHz -- matches the server's VAD frame size

const WORKLET_CODE = `
  class MicProcessor extends AudioWorkletProcessor {
    constructor() {
      super();
      this.buffer = new Float32Array(${FRAME_SAMPLES});
      this.offset = 0;
      this.port.onmessage = this.onMessage.bind(this);
    }
    onMessage(e) {
      if (e.data && e.data.type === "stop") {
        this.port.postMessage({ type: "stop" });
        this.port.close();
      }
    }
    process(inputs) {
      const input = inputs[0];
      if (!input || !input.length) return true;
      const channel = input[0];
      for (let i = 0; i < channel.length; i++) {
        this.buffer[this.offset] = channel[i];
        this.offset++;
        if (this.offset >= ${FRAME_SAMPLES}) {
          const int16 = new Int16Array(${FRAME_SAMPLES});
          let sumSq = 0;
          for (let j = 0; j < ${FRAME_SAMPLES}; j++) {
            const s = this.buffer[j];
            const clamped = s < -1 ? -1 : s > 1 ? 1 : s;
            int16[j] = clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;
            sumSq += clamped * clamped;
          }
          const rms = Math.sqrt(sumSq / ${FRAME_SAMPLES});
          this.port.postMessage({ data: int16.buffer, rms: rms }, [int16.buffer]);
          this.offset = 0;
        }
      }
      return true;
    }
  }
  registerProcessor("mic-processor", MicProcessor);
`;

/** Register the worklet module via a Blob URL (no separate static asset needed) and
 * return an AudioWorkletNode ready to connect. */
export async function createMicWorkletNode(audioCtx: AudioContext): Promise<AudioWorkletNode> {
  const blob = new Blob([WORKLET_CODE], { type: "application/javascript" });
  const url = URL.createObjectURL(blob);
  try {
    await audioCtx.audioWorklet.addModule(url);
  } finally {
    URL.revokeObjectURL(url);
  }
  return new AudioWorkletNode(audioCtx, "mic-processor");
}

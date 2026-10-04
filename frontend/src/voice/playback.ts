// Gapless PCM16 playback scheduling, ported from web/mic-test.html's
// schedulePlayback()/stopAllPlayback() (there called against a `call` object; here a
// small class holds the same two fields: nextPlay, activeSources).
import { SAMPLE_RATE } from "./audioWorklet";
import { decodePCM16 } from "./pcm";

export class AudioPlayer {
  private nextPlay = 0;
  private activeSources: AudioBufferSourceNode[] = [];

  constructor(private audioCtx: AudioContext) {}

  /** Schedule one PCM16 chunk right after whatever is already queued ("gapless").
   * `onDrained` fires once, after this chunk finishes, only if nothing is still
   * queued/playing past it -- matches the original's "bot finished speaking" check. */
  schedule(pcm: ArrayBuffer, onDrained: () => void): void {
    const samples = decodePCM16(pcm);
    const buf = this.audioCtx.createBuffer(1, samples.length, SAMPLE_RATE);
    buf.getChannelData(0).set(samples);

    const src = this.audioCtx.createBufferSource();
    src.buffer = buf;
    src.connect(this.audioCtx.destination);

    const dur = samples.length / SAMPLE_RATE;
    this.nextPlay = Math.max(this.nextPlay, this.audioCtx.currentTime);
    src.start(this.nextPlay);
    this.nextPlay += dur;

    src.onended = () => {
      const idx = this.activeSources.indexOf(src);
      if (idx >= 0) this.activeSources.splice(idx, 1);
      if (this.nextPlay <= this.audioCtx.currentTime + 0.05) onDrained();
    };
    this.activeSources.push(src);
  }

  stopAll(): void {
    for (const src of this.activeSources) {
      try {
        src.stop();
      } catch {
        // already stopped/ended -- fine
      }
    }
    this.activeSources = [];
    this.nextPlay = 0;
  }
}

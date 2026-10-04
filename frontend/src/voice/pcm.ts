// Pure PCM16 <-> Float32 conversion helpers, kept framework/Web-Audio free so they can
// be unit tested directly. `audioWorklet.ts`'s WORKLET_CODE string duplicates the
// encode half of this (AudioWorkletGlobalScope can't import ES modules), written to
// match clampAndEncodeSample() exactly -- keep the two in sync if either changes.

/** Clamp a float sample to [-1, 1] and encode it as an Int16 PCM sample (same formula
 * as the original inline worklet: negative side uses 0x8000, positive uses 0x7fff). */
export function clampAndEncodeSample(sample: number): number {
  const clamped = sample < -1 ? -1 : sample > 1 ? 1 : sample;
  return clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;
}

export function encodePCM16(float32: Float32Array): Int16Array {
  const out = new Int16Array(float32.length);
  for (let i = 0; i < float32.length; i++) out[i] = clampAndEncodeSample(float32[i]);
  return out;
}

/** Decode a PCM16 LE buffer (as received over the WebSocket) into Float32 samples in
 * [-1, 1), for scheduling into a Web Audio buffer. */
export function decodePCM16(buffer: ArrayBuffer): Float32Array {
  const view = new Int16Array(buffer);
  const out = new Float32Array(view.length);
  for (let i = 0; i < view.length; i++) out[i] = view[i] / 0x8000;
  return out;
}

export function rms(float32: Float32Array): number {
  let sumSq = 0;
  for (let i = 0; i < float32.length; i++) sumSq += float32[i] * float32[i];
  return Math.sqrt(sumSq / float32.length);
}

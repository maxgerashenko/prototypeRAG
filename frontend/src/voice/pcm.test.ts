import { describe, expect, it } from "vitest";
import { clampAndEncodeSample, decodePCM16, encodePCM16, rms } from "./pcm";

describe("clampAndEncodeSample", () => {
  it("maps full-scale samples to the Int16 extremes", () => {
    expect(clampAndEncodeSample(1)).toBeCloseTo(0x7fff);
    expect(clampAndEncodeSample(-1)).toBeCloseTo(-0x8000);
  });
  it("clamps out-of-range samples instead of wrapping", () => {
    expect(clampAndEncodeSample(2)).toBeCloseTo(0x7fff);
    expect(clampAndEncodeSample(-2)).toBeCloseTo(-0x8000);
  });
  it("maps silence to zero", () => {
    expect(clampAndEncodeSample(0)).toBe(0);
  });
});

describe("encodePCM16 / decodePCM16 round trip", () => {
  it("round-trips representative samples within quantization error", () => {
    const original = new Float32Array([0, 0.5, -0.5, 1, -1, 0.25]);
    const encoded = encodePCM16(original);
    const buffer = new Int16Array(encoded).buffer;
    const decoded = decodePCM16(buffer);
    for (let i = 0; i < original.length; i++) {
      expect(decoded[i]).toBeCloseTo(original[i], 3);
    }
  });
});

describe("rms", () => {
  it("is zero for silence and positive for a constant tone", () => {
    expect(rms(new Float32Array([0, 0, 0]))).toBe(0);
    expect(rms(new Float32Array([0.5, -0.5, 0.5, -0.5]))).toBeCloseTo(0.5);
  });
});

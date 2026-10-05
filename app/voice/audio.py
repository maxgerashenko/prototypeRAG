"""Audio format helpers for the voice channel. Twilio sends 8 kHz μ-law; VAD/STT/browser/Gemini Live use PCM16 at various rates."""

import numpy as np

TWILIO_SAMPLE_RATE: int = 8000

# int32, not uint8: `<< _exp` below overflows 8 bits (draft bug caught by the audioop test).
_idx = np.arange(256, dtype=np.int32)
_u = ~_idx & 0xFF
_sign = _u & 0x80
_exp = (_u >> 4) & 0x07
_man = _u & 0x0F
_sample = (((_man << 3) + 0x84) << _exp) - 0x84
_ULAW_DECODE_TABLE = np.where(_sign, -_sample, _sample).astype(np.int16)


def ulaw_to_pcm16(data: bytes) -> bytes:
    """Decode G.711 μ-law bytes to PCM16."""
    if not data:
        return b""
    idx = np.frombuffer(data, dtype=np.uint8)
    return _ULAW_DECODE_TABLE[idx].astype('<i2').tobytes()


def pcm16_to_ulaw(data: bytes) -> bytes:
    """Encode PCM16 bytes to G.711 μ-law."""
    if not data:
        return b""
    s32 = np.frombuffer(data, dtype='<i2').astype(np.int32)
    sign = np.where(s32 < 0, 0x80, 0)
    magnitude = np.minimum(np.abs(s32), 32635) + 0x84
    mag_shifted = magnitude >> 7
    thresholds = np.array([1, 2, 4, 8, 16, 32, 64, 128], dtype=np.int32)
    exponent = np.clip(np.searchsorted(thresholds, mag_shifted, side='right') - 1, 0, 7)
    mantissa = (magnitude >> (exponent + 3)) & 0x0F
    u = ~(sign | (exponent << 4) | mantissa) & 0xFF
    return u.astype(np.uint8).tobytes()


def resample_pcm16(data: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Resample a PCM16 chunk. Each chunk is processed independently, so chunk edges get small artifacts — fine for speech recognition, not hi-fi."""
    if not data:
        return b""
    if src_rate == dst_rate:
        return data
    x = np.frombuffer(data, dtype='<i2').astype(np.float64)
    if dst_rate < src_rate:
        fc = 0.45 * dst_rate / src_rate
        n_taps = 63
        mid = 31
        n = np.arange(n_taps)
        h = 2 * fc * np.sinc(2 * fc * (n - mid))
        h *= 0.54 - 0.46 * np.cos(2 * np.pi * n / (n_taps - 1))
        h /= np.sum(h)
        x = np.convolve(x, h, mode="same")
    n_out = round(len(x) * dst_rate / src_rate)
    n_out = max(n_out, 1)
    t = np.arange(n_out) * src_rate / dst_rate
    x_out = np.interp(t, np.arange(len(x), dtype=np.float64), x)
    x_out = np.clip(np.round(x_out).astype(np.int32), -32768, 32767)
    return x_out.astype('<i2').tobytes()


def working_sound(sample_rate: int, loop_s: float = 1.0) -> bytes:
    """One loop of the quiet "working on it" sound (T1, plan/03-voice-channel.md): two soft
    ticks per second, PCM16 mono. Generated here, not a third-party clip, so there is no
    licensing question. Short decaying sine bursts at ~-24 dBFS peak: well under speech
    level, and too short and sparse for the VAD to take as speech (checked in the tests)."""
    n = round(sample_rate * loop_s)
    out = np.zeros(n, dtype=np.float64)
    tick_n = round(sample_rate * 0.025)  # 25 ms
    t = np.arange(tick_n) / sample_rate
    for start_s, freq in ((0.0, 1000.0), (loop_s / 2, 800.0)):  # tick ... tock
        tick = np.sin(2 * np.pi * freq * t) * np.exp(-t / 0.006)
        start = round(start_s * sample_rate)
        out[start:start + tick_n] += tick[: n - start]
    return np.round(out * 0.06 * 32767).astype('<i2').tobytes()

#!/usr/bin/env python3
"""Synthesised transition SFX. Generated from noise/sines, so nothing is licensed.

Builds one silent-bed WAV with sounds placed at given times; render.py mixes that
bed in as a single input instead of one ffmpeg input per hit.
"""
import wave
from pathlib import Path
import numpy as np

SR = 48000


def _svf(x, cutoff, q=1.6):
    """Chamberlin state-variable filter, per-sample cutoff array. Returns bandpass."""
    f = 2 * np.sin(np.pi * np.clip(cutoff, 20, SR * 0.45) / SR)
    low = band = 0.0
    out = np.empty_like(x)
    for i in range(len(x)):
        high = x[i] - low - q * band
        band += f[i] * high
        low += f[i] * band
        out[i] = band
    return out


def _env(n, attack=0.02, release=0.6, power=2.0):
    a = max(1, int(attack * n))
    e = np.concatenate([np.linspace(0, 1, a), np.linspace(1, 0, n - a) ** power])
    return e[:n]


def whoosh(dur=0.55, up=True, seed=0):
    n = int(dur * SR)
    rng = np.random.default_rng(seed)
    noise = rng.standard_normal(n)
    sweep = np.linspace(400, 5200, n) if up else np.linspace(5200, 400, n)
    y = _svf(noise, sweep, q=1.1) * _env(n, 0.18, power=1.6)
    return y


def impact(dur=1.1, seed=1):
    n = int(dur * SR)
    t = np.arange(n) / SR
    freq = 130 * np.exp(-t * 7) + 38                     # pitch drop
    body = np.sin(2 * np.pi * np.cumsum(freq) / SR) * np.exp(-t * 4.5)
    rng = np.random.default_rng(seed)
    click = _svf(rng.standard_normal(n), np.full(n, 2400), q=0.8) * np.exp(-t * 55) * 0.5
    return body * 0.9 + click


def tick(dur=0.07, seed=2):
    n = int(dur * SR)
    rng = np.random.default_rng(seed)
    return _svf(rng.standard_normal(n), np.full(n, 3200), q=0.6) * _env(n, 0.05, power=3.0)


def riser(dur=1.6, seed=3):
    n = int(dur * SR)
    t = np.arange(n) / SR
    rng = np.random.default_rng(seed)
    sweep = 300 * np.exp(t / dur * 2.9)                  # exponential climb
    y = _svf(rng.standard_normal(n), sweep, q=2.2)
    return y * (t / dur) ** 1.7


KINDS = {"whoosh": whoosh, "whoosh_down": lambda **k: whoosh(up=False, **k),
         "impact": impact, "tick": tick, "riser": riser}
_cache = {}


def _get(kind):
    if kind not in _cache:
        y = KINDS[kind]()
        peak = np.max(np.abs(y)) or 1.0
        _cache[kind] = (y / peak * 0.5).astype(np.float32)   # ~-6 dBFS
    return _cache[kind]


def bed(events, total, path):
    """events: [{'t': sec, 'kind': str, 'gain': float}] -> mono 48k WAV at `path`."""
    buf = np.zeros(int(total * SR) + SR, dtype=np.float32)
    for e in events:
        y = _get(e["kind"]) * float(e.get("gain", 1.0))
        i = int(float(e["t"]) * SR)
        if i < 0:
            y, i = y[-i:], 0
        buf[i:i + len(y)] += y[:len(buf) - i]
    peak = float(np.max(np.abs(buf)))
    if peak > 0.89:
        buf *= 0.89 / peak
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((buf * 32767).astype("<i2").tobytes())
    return path


def _selftest():
    import tempfile
    for k in KINDS:
        y = _get(k)
        assert len(y) > 1000 and np.isfinite(y).all(), k
        assert 0.4 < float(np.max(np.abs(y))) <= 0.5, (k, float(np.max(np.abs(y))))
    p = Path(tempfile.mkdtemp()) / "bed.wav"
    bed([{"t": 0.5, "kind": "whoosh"}, {"t": 0.6, "kind": "impact", "gain": 0.8}], 3.0, p)
    with wave.open(str(p)) as w:
        assert w.getframerate() == SR and w.getnframes() == int(3.0 * SR) + SR
    # overlapping hits must not clip
    a = np.frombuffer(open(p, "rb").read()[44:], dtype="<i2")
    assert np.max(np.abs(a)) < 32767, "clipped"
    print("sfx.py selftest ok")


if __name__ == "__main__":
    _selftest()

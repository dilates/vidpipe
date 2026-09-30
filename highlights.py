#!/usr/bin/env python3
"""Find candidate hook moments: loud + fast-talking + excited wording.

Ranks 8s windows so the editorial pass has somewhere to look instead of reading
a 20-minute transcript cold. Signal only — the actual pick is a judgement call.
"""
import subprocess
import numpy as np

SR = 16000
EXCITED = ("oh", "wow", "whoa", "insane", "crazy", "holy", "no way", "look at that",
           "watch this", "let's go", "clutch", "actually", "wait", "what the",
           "here's the thing", "most people", "nobody tells you", "the trick",
           "turns out", "this is the part", "you don't need", "never", "finally")


def envelope(src, win=0.25):
    """Per-window RMS of the mix."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", src, "-vn", "-ac", "1",
                          "-ar", str(SR), "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768
    n = int(win * SR)
    x = x[:len(x) // n * n].reshape(-1, n)
    return np.sqrt((x ** 2).mean(axis=1) + 1e-12), win


def candidates(src, words, span=8.0, top=12):
    rms, win = envelope(src)
    if not len(rms):
        return []
    loud = rms / (np.percentile(rms, 99) or 1.0)
    per = max(1, int(span / win))
    text = " ".join(w["word"].lower() for w in words)
    del text                                                   # per-window below

    scores = []
    for i in range(0, len(loud) - per, max(1, per // 2)):
        t0, t1 = i * win, (i + per) * win
        seg = [w for w in words if t0 <= w["start"] < t1]
        if not seg:
            continue
        line = " ".join(w["word"].lower().strip(".,!?") for w in seg)
        excite = sum(line.count(k) for k in EXCITED)
        density = min(len(seg) / span / 3.2, 1.0)              # words/sec, capped
        score = float(loud[i:i + per].mean()) * 1.0 + excite * 0.55 + density * 0.8
        scores.append({"at": round(t0, 1), "end": round(t1, 1), "score": round(score, 3),
                       "text": " ".join(w["word"] for w in seg)[:220]})

    scores.sort(key=lambda s: -s["score"])
    picked = []
    for s in scores:                                            # non-overlapping
        if all(s["at"] >= p["end"] or s["end"] <= p["at"] for p in picked):
            picked.append(s)
        if len(picked) >= top:
            break
    return sorted(picked, key=lambda s: -s["score"])


def _selftest():
    w = [{"start": i * 0.3, "end": i * 0.3 + 0.25, "word": x}
         for i, x in enumerate("this is insane no way look at that".split())]
    line = " ".join(x["word"] for x in w).lower()
    assert sum(line.count(k) for k in EXCITED) >= 2
    print("highlights.py selftest ok")


if __name__ == "__main__":
    _selftest()

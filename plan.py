#!/usr/bin/env python3
"""Raw footage + word-level transcript -> cut plan (ranges to KEEP).

Keeps a moment if there is speech OR the mix is loud (gameplay action with no
commentary). Everything quiet and wordless gets cut, minus a breath at each seam.
"""
import argparse, json, re, subprocess, sys
from pathlib import Path

import highlights

FILLERS = {"um", "uh", "erm", "uhh", "umm", "hmm", "mhm", "like,"}


# ---------- interval helpers ----------
def merge(iv, slack=0.0):
    iv = sorted(iv)
    out = []
    for s, e in iv:
        if out and s - out[-1][1] <= slack:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return out


def invert(iv, lo, hi):
    out, cur = [], lo
    for s, e in iv:
        if s > cur:
            out.append([cur, min(s, hi)])
        cur = max(cur, e)
    if cur < hi:
        out.append([cur, hi])
    return [x for x in out if x[1] > x[0]]


# ---------- probes ----------
def duration(src):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", src], capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


def silences(src, noise=-32, mindur=0.35):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", src,
                        "-af", f"silencedetect=n={noise}dB:d={mindur}", "-f", "null", "-"],
                       capture_output=True, text=True)
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", r.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: (-?[\d.]+)", r.stderr)]
    return merge([[s, e] for s, e in zip(starts, ends)])


def words_of(tj):
    """Flatten whisper-ctranslate2 json to [{start,end,word}]."""
    out = []
    for seg in tj.get("segments", []):
        for w in seg.get("words") or []:
            out.append({"start": float(w["start"]), "end": float(w["end"]),
                        "word": (w.get("word") or w.get("text") or "").strip()})
    return out


# ---------- planning ----------
def speech_blocks(words, gap=0.40, pad_in=0.12, pad_out=0.32, drop_fillers=True):
    keep = []
    for i, w in enumerate(words):
        if drop_fillers and w["word"].lower().strip(".,?!") in FILLERS:
            prev_gap = w["start"] - words[i - 1]["end"] if i else 9
            next_gap = words[i + 1]["start"] - w["end"] if i + 1 < len(words) else 9
            if prev_gap > 0.12 and next_gap > 0.12:      # isolated -> safe to remove
                continue
        keep.append([w["start"] - pad_in, w["end"] + pad_out])
    return merge([[max(0, s), e] for s, e in keep], slack=gap)


def build(src, words, min_cut=0.5, breath=0.22, noise=-32, mode="gameplay"):
    """gameplay: keep speech OR loud action.  talking: keep speech only."""
    dur = duration(src)
    audible = invert(silences(src, noise), 0, dur) if mode == "gameplay" else []
    keep = merge(speech_blocks(words) + audible, slack=0.05)
    # tiny gaps aren't worth a cut; real gaps keep a breath at each seam
    out = []
    for s, e in keep:
        if out:
            gap = s - out[-1][1]
            if gap < min_cut:
                out[-1][1] = e
                continue
            out[-1][1] += breath / 2
            s -= breath / 2
        out.append([s, e])
    out = [[max(0, s), min(dur, e)] for s, e in out]
    return snap_clips(out, words), dur


def flatten(timeline):
    """Timeline sections -> clip ranges in OUTPUT order (may repeat or jump back)."""
    return [c for sec in timeline for c in sec["clips"]]


def snap_clips(clips, words, tail=0.15):
    """Never let a cut land inside a word, and keep the last word's tail.

    Whisper's word end times clip consonants, so cutting at end+pad chops the tail
    and sounds like the speaker was cut off mid-sentence.
    """
    out = []
    for s, e in clips:
        for w in words:                       # only one word can contain a given time
            if w["start"] + 0.02 < s < w["end"] - 0.02:
                s = w["end"]                  # starts mid-word -> drop the partial
            if w["start"] + 0.02 < e < w["end"] - 0.02:
                e = w["start"]                # ends mid-word -> exclude it
        inside = [w for w in words if w["start"] >= s and w["end"] <= e]
        if inside:
            nxt = [w for w in words if w["start"] >= e]
            limit = nxt[0]["start"] - 0.03 if nxt else e + tail
            e = max(e, min(inside[-1]["end"] + tail, limit))
        if e - s > 0.15:
            out.append([round(s, 3), round(e, 3)])
    return out


def remap(words, keep):
    """Shift word times onto the post-cut timeline; drop words that fell in a cut."""
    out, base = [], 0.0
    for s, e in keep:
        for w in words:
            if w["start"] >= s and w["end"] <= e:
                out.append({"start": round(w["start"] - s + base, 3),
                            "end": round(w["end"] - s + base, 3), "word": w["word"]})
        base += e - s
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source"); ap.add_argument("transcript")
    ap.add_argument("-o", "--out", default="plan.json")
    ap.add_argument("--min-cut", type=float, default=0.5)
    ap.add_argument("--breath", type=float, default=0.22)
    ap.add_argument("--noise", type=int, default=-32)
    ap.add_argument("--mode", choices=["gameplay", "talking"], default="gameplay",
                    help="gameplay keeps loud action without commentary; talking cuts every pause")
    a = ap.parse_args()

    words = words_of(json.loads(Path(a.transcript).read_text()))
    keep, dur = build(a.source, words, a.min_cut, a.breath, a.noise, a.mode)
    kept = sum(e - s for s, e in keep)

    # One section covering everything, in source order. The editorial pass splits
    # and reorders these for retention; render.py plays them in list order.
    timeline = [{"name": "body", "clips": keep, "speed": 1.0}]
    plan = {"source": str(Path(a.source).resolve()), "output": "final.mp4",
            "mode": a.mode, "timeline": timeline, "captions": True, "accent": "#3ddc97",
            "graphics": [], "zooms": [], "chapters": [], "sfx": "auto",
            "music": None, "music_db": -22,
            "words_raw": words,
            "transcript": remap(words, flatten(timeline)),
            "hook_candidates": highlights.candidates(a.source, words),
            "stats": {"raw": round(dur, 1), "cut": round(kept, 1),
                      "removed_pct": round(100 * (1 - kept / dur), 1), "cuts": len(keep) - 1}}
    Path(a.out).write_text(json.dumps(plan, indent=1))
    print(f"{dur:.0f}s -> {kept:.0f}s  ({plan['stats']['removed_pct']}% removed, "
          f"{plan['stats']['cuts']} cuts)  -> {a.out}")
    print(f"{len(plan['hook_candidates'])} hook candidates ranked")


def _selftest():
    assert merge([[0, 1], [1.2, 2], [5, 6]], slack=0.3) == [[0, 2], [5, 6]]
    assert invert([[1, 2]], 0, 3) == [[0, 1], [2, 3]]
    w = [{"start": 0.0, "end": 0.5, "word": "hi"}, {"start": 9.0, "end": 9.5, "word": "there"}]
    assert remap(w, [[0, 1], [8.9, 10]]) == [
        {"start": 0.0, "end": 0.5, "word": "hi"}, {"start": 1.1, "end": 1.6, "word": "there"}]
    sb = speech_blocks([{"start": 1, "end": 1.2, "word": "so"},
                        {"start": 1.5, "end": 1.7, "word": "um"},
                        {"start": 2.0, "end": 2.4, "word": "anyway"}])
    sb = [[round(x, 3) for x in b] for b in sb]
    assert sb == [[0.88, 2.72]], sb          # filler dropped, neighbours bridged
    tl = [{"name": "hook", "clips": [[50, 58]]}, {"name": "body", "clips": [[0, 10], [12, 20]]}]
    assert flatten(tl) == [[50, 58], [0, 10], [12, 20]]
    ws = [{"start": 1.0, "end": 1.5, "word": "hello"}, {"start": 2.0, "end": 2.6, "word": "world"}]
    sc = snap_clips([[1.2, 2.3]], ws)                 # both ends bisect a word
    assert sc[0][0] == 1.5 and sc[0][1] == 2.0, sc    # pulled out to word edges
    sc2 = snap_clips([[0.9, 1.52]], ws)               # tail barely after the word
    assert sc2[0][1] >= 1.6, sc2                      # tail preserved
    print("plan.py selftest ok")


if __name__ == "__main__":
    (_selftest if "--selftest" in sys.argv else main)()

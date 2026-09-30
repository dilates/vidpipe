#!/usr/bin/env python3
"""Fill a cut plan with conservative, deterministic graphics — the no-LLM path.

Writes only what an editorial pass would otherwise leave empty: title card, a
CTA beat, an end screen, chapter marks, thumbnail hook text, and the detected
subject position for shorts cropping. Uses verbatim transcript text only, so it
can't put words in the speaker's mouth. Anything already present in plan.json
(an agent's or your edits) is left untouched, and the timeline is never
reordered — restructuring is a judgement call, not a rule.
"""
import json, re, subprocess, sys
from pathlib import Path

import numpy as np
import render

FILLERS = {"um", "uh", "erm", "hmm", "like", "so", "and", "but", "actually"}


# ---------- helpers ----------
def trim(text, n):
    """Cut at a word boundary, no dangling punctuation."""
    text = " ".join(text.split())
    if len(text) <= n:
        return text
    cut = text[:n].rsplit(" ", 1)[0].rstrip(",;:.!?")
    return (cut or text[:n]) + "…"


def sentences_out(words):
    out, cur = [], []
    for w in words:
        cur.append(w["word"])
        if w["word"].endswith((".", "?", "!")):
            out.append(" ".join(cur)); cur = []
    if cur:
        out.append(" ".join(cur))
    return [s for s in out if s.strip()]


def output_starts(timeline):
    """Output-timeline start time of each section."""
    out, t = [], 0.0
    for sec in timeline:
        out.append(t)
        t += render.section_dur(sec)
    return out


def source_to_output(plan, t):
    """Map a source-timeline second onto the cut timeline (None if it was cut)."""
    base = 0.0
    for sec in plan["timeline"]:
        sp = float(sec.get("speed", 1.0) or 1.0)
        for s, e in sec["clips"]:
            if s <= t <= e:
                return round(base + (t - s) / sp, 2)
            base += (e - s) / sp
    return None


# ---------- subject position ----------
def _centroid(src, win):
    """Motion centroid x in 0..1 at one sampling density, or None if nothing moves.

    Greyscale frames sampled every `win` seconds, abs-diffed, collapsed to
    columns. The speaker moves, the room doesn't.
    """
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", src, "-vf",
                        f"fps=1/{win},scale=64:36,format=gray", "-f", "rawvideo",
                        "-pix_fmt", "gray", "-"], capture_output=True, check=True)
    fr = np.frombuffer(r.stdout, np.uint8).reshape(-1, 36, 64).astype(np.float32)
    if len(fr) < 2:
        return None
    cols = np.abs(fr[1:] - fr[:-1]).sum(axis=(0, 1))          # per-column motion
    if cols.sum() <= 0:
        return None
    cx = (cols * (np.arange(64) + 0.5)).sum() / cols.sum() / 64
    return round(float(min(max(cx, 0.05), 0.95)), 3)


def detect_subject(src, win=12.0):
    """Sample at `win`, then dense; a locked-off shot with a barely-moving
    speaker can alias a sparse grid into 'nothing moves'."""
    return _centroid(src, win) or _centroid(src, 2.0) or 0.5


# ---------- autodress ----------
def notable(text, at, total, used):
    """A verbatim sentence worth putting on screen: has a number or a strong claim."""
    if not (0.12 * total < at < 0.85 * total):
        return False
    if any(abs(at - u) < 20 for u in used):
        return False
    low = text.lower()
    return bool(re.search(r"\d|\bpercent\b|never|always|nobody|most people|turns out|"
                          r"the trick|faster|slower|cheap|free|impossible|easy", low))


def hook_phrase(plan):
    """Strongest verbatim hook whose first sentence is a real sentence."""
    for c in plan.get("hook_candidates", []):
        s = (c.get("text") or "").split(".")[0].strip()
        if len(s.split()) >= 4:
            return s
    return ""


def fill(plan):
    total = render.total_dur(plan["timeline"])
    words = render.remap(plan.get("words_raw", []), plan["timeline"])
    mode = plan.get("mode", "talking")
    sents = sentences_out(words)

    if "subject_cx" not in plan:
        plan["subject_cx"] = 0.5 if mode == "gameplay" else detect_subject(plan["source"])
        print(f"subject_cx {plan['subject_cx']} ({'detected' if mode != 'gameplay' else 'gameplay centre'})")

    # title: the first clean sentence is the honest guess
    if not plan.get("title"):
        first = next((s for s in sents if len(s) > 8), "")
        plan["title"] = trim(first.lstrip(".,!? "), 60)

    if not plan.get("chapters"):
        starts = output_starts(plan["timeline"])
        if len(plan["timeline"]) > 1:
            plan["chapters"] = [{"t": round(t, 1), "name": sec.get("name", "part").replace("_", " ").capitalize()}
                                for t, sec in zip(starts, plan["timeline"])]
        elif plan["title"]:
            plan["chapters"] = [{"t": 0.0, "name": plan["title"]}]

    if not plan.get("graphics"):
        g = []
        g.append({"template": "title", "at": 1.0, "dur": 3.5, "anim": "fade",
                  "vars": {"kicker": mode.upper(), "title": trim(plan["title"], 46), "sub": ""}})
        title_end = 4.5
        end_at = max(title_end + 1.0, total - 14.0)
        end_dur = min(13.5, total - end_at - 0.5)
        cta_at = round(total * 0.45, 1)
        kp_hi = end_at - 5.5
        if total >= 30 and cta_at + 5.0 < end_at - 1.0:       # too short for two mid-roll beats
            g.append({"template": "cta", "at": cta_at, "dur": 5.0, "anim": "slide_up",
                      "vars": {"text": "Subscribe", "sub": "if this was useful"}})
            kp_hi = min(cta_at - 5.5, kp_hi)
        kp_hi = min(cta_at - 5.5, end_at - 5.5)
        if mode == "talking":                                 # verbatim key-point cards
            used = []
            for s in sents:
                at = next((w["start"] for w in words if s.startswith(w["word"])), None)
                if at is None:
                    continue
                t_out = source_to_output(plan, at)
                if t_out is None or not (title_end + 0.5 <= t_out <= kp_hi):
                    continue
                if notable(s, t_out, total, used):
                    used.append(t_out)
                    g.append({"template": "lower_third", "at": round(t_out + 0.5, 1),
                              "dur": 5.0, "anim": "slide_left",
                              "vars": {"kicker": "KEY POINT", "title": trim(s, 48), "sub": ""}})
                if len(used) >= 2:
                    break
        if end_dur >= 3.0:
            g.append({"template": "endscreen", "at": round(end_at, 1), "dur": round(end_dur, 1),
                      "anim": "fade",
                      "vars": {"title": "Thanks for watching", "sub": "What should the next video cover?",
                               "cta": "Subscribe", "cta2": "Leave a comment"}})
        plan["graphics"] = g
        print(f"graphics: {len(g)} cards (title, key points, CTA, end screen)")

    if not plan.get("thumbnail"):
        plan["thumbnail"] = {"top": mode.upper(), "big": trim(hook_phrase(plan) or plan["title"], 30),
                             "sub": ""}

    return plan


def main():
    plan_path = Path(sys.argv[1])
    plan = json.loads(plan_path.read_text())
    before = dict(plan.get("stats", {}))
    fill(plan)
    plan_path.write_text(json.dumps(plan, indent=1))
    s = plan["stats"]
    print(f"plan: {s['cut']}s of {s['raw']}s ({s['removed_pct']}% removed, {s['cuts']} cuts)"
          if before else "plan dressed")


def _selftest():
    import tempfile
    tl = [{"name": "body", "clips": [[0, 40], [60, 100]], "speed": 1.0}]   # 80s output
    words = [{"start": 0.2, "end": 0.7, "word": "This"}, {"start": 0.7, "end": 1.4, "word": "works."},
             {"start": 25.0, "end": 25.5, "word": "It's"}, {"start": 25.5, "end": 26.0, "word": "30%"},
             {"start": 26.0, "end": 26.8, "word": "faster."}]
    plan = {"source": "/dev/null", "output": "final.mp4", "mode": "talking", "timeline": tl,
            "words_raw": words, "graphics": [], "chapters": [], "hook_candidates": [],
            "stats": {"raw": 120, "cut": 80, "removed_pct": 33, "cuts": 1}}
    detect_subject = globals()["detect_subject"]
    globals()["detect_subject"] = lambda src, win=12.0: 0.34   # no ffmpeg in the selftest
    try:
        plan2 = fill(dict(plan))
    finally:
        globals()["detect_subject"] = detect_subject
    tpl = [g["template"] for g in plan2["graphics"]]
    assert tpl[0] == "title" and "cta" in tpl and tpl[-1] == "endscreen", tpl
    assert all("title" in g["vars"] for g in plan2["graphics"] if g["template"] == "title")
    kp = [g for g in plan2["graphics"] if g["template"] == "lower_third"]
    assert kp and "faster" in kp[0]["vars"]["title"].lower(), "key point must be verbatim"
    assert abs(kp[0]["at"] - 25.5) < 0.01, kp[0]      # source 25.0 -> output 25.0 (+0.5)
    assert plan2["chapters"] == [{"t": 0.0, "name": plan2["title"]}]
    assert plan2["thumbnail"]["big"] != ""
    assert plan2["subject_cx"] == 0.34
    # short video: no CTA, end screen still fits, nothing overlaps
    short = {**plan, "timeline": [{"name": "body", "clips": [[0, 20]], "speed": 1.0}],
             "words_raw": words[:2]}
    detect_subject_saved = globals()["detect_subject"]
    globals()["detect_subject"] = lambda src, win=12.0: 0.34
    try:
        plan3 = fill(dict(short))
    finally:
        globals()["detect_subject"] = detect_subject_saved
    assert not [g for g in plan3["graphics"] if g["template"] == "cta"], "CTA on a 20s video"
    assert plan3["graphics"][-1]["template"] == "endscreen"
    sorted_g = sorted(plan3["graphics"], key=lambda x: x["at"])
    for a, b in zip(sorted_g, sorted_g[1:]):
        assert a["at"] + a["dur"] <= b["at"], (a, b)
    assert trim("one two three four five", 12) == "one two…"
    assert hook_phrase({"hook_candidates": [{"text": "video. Here's the thing."},
                                            {"text": "Most people never check this setting."}]}) \
        == "Most people never check this setting"
    assert source_to_output(plan, 80) == 60.0 and source_to_output(plan, 50) is None
    print("autodress.py selftest ok")


if __name__ == "__main__":
    (_selftest if "--selftest" in sys.argv else main)()
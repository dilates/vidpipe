#!/usr/bin/env python3
"""Validate a plan before render — the pre-flight the render can't do for you.

Checks what actually bites: cuts landing inside words, graphics overlapping or
running past the end, zooms compounding (their expressions sum, so two at once
lurch), chapters out of order, and templates that don't exist. Exit 1 on error.
"""
import json, sys
from pathlib import Path

import render, plan as planner

BOTTOM = {"lower_third", "cta"}


def check(plan):
    errs, warns = [], []

    src = plan.get("source")
    if not src or not Path(src).exists():
        errs.append(f"source missing: {src}")
        return errs, warns
    if not plan.get("output"):
        errs.append("no output path")
    tl = plan.get("timeline") or []
    if not tl:
        errs.append("no timeline")
        return errs, warns

    try:
        dur = planner.duration(src)
    except Exception as e:                                   # noqa: BLE001
        errs.append(f"can't probe source: {e}")
        dur = None
    total = render.total_dur(tl)

    clips = render.flatten(tl)
    for s, e in clips:
        if e <= s:
            errs.append(f"empty clip [{s}, {e}]")
    if dur:
        for s, e in clips:
            if s < 0 or e > dur:
                errs.append(f"clip [{s}, {e}] outside source ({dur:.1f}s)")
    for sec in tl:
        sp = float(sec.get("speed", 1.0) or 1.0)
        if not 0.1 <= sp <= 8:
            errs.append(f"section '{sec.get('name')}' speed {sp} outside 0.1-8")

    words = plan.get("words_raw") or []
    if words:
        for s, e in clips:
            for w in words:
                if w["start"] + 0.02 < s < w["end"] - 0.02 or w["start"] + 0.02 < e < w["end"] - 0.02:
                    errs.append(f"cut inside word '{w['word']}' at {w['start']:.1f} "
                                f"(clip [{s}, {e}]) — run plan.snap_clips()")

    g = plan.get("graphics") or []
    prev = None
    for gr in sorted(g, key=lambda x: x["at"]):
        t, d = float(gr["at"]), float(gr.get("dur", 4.0))
        tpl = gr.get("template", "")
        if not (render.ROOT / "templates" / f"{tpl}.html").exists():
            errs.append(f"unknown template '{tpl}'")
        if t < 0:
            errs.append(f"{tpl} at {t} < 0")
        if t + d > total + 0.01:
            errs.append(f"{tpl} runs to {t + d:.1f}, past the {total:.1f}s end")
        if prev and t < prev[0] + prev[1]:
            errs.append(f"{tpl} at {t} overlaps {prev[2]} ending {prev[0] + prev[1]:.1f}")
        prev = (t, d, tpl)
    z = plan.get("zooms") or []
    prevz = None
    for k in sorted(z, key=lambda x: x["at"]):
        t, d = float(k["at"]), float(k.get("dur", 3.0))
        if 1.0 >= float(k.get("scale", 1.25)) or float(k.get("scale", 1.25)) > 1.6:
            warns.append(f"zoom scale {k.get('scale')} outside the sane 1.05-1.6 band")
        if not 0 <= float(k.get("cx", 0.5)) <= 1 or not 0 <= float(k.get("cy", 0.5)) <= 1:
            errs.append(f"zoom cx/cy outside 0..1: {k}")
        if prevz and t < prevz[0] + prevz[1]:
            errs.append(f"zoom at {t} overlaps zoom ending {prevz[0] + prevz[1]:.1f} "
                        "— zoom expressions SUM, two at once compound into a lurch")
        prevz = (t, d)

    ch = plan.get("chapters") or []
    last = -1.0
    for c in ch:
        if float(c["t"]) < last:
            errs.append(f"chapter '{c['name']}' at {c['t']} breaks order (after {last})")
        last = float(c["t"])
        if float(c["t"]) > total:
            errs.append(f"chapter '{c['name']}' at {c['t']} past the {total:.1f}s end")
    if ch and float(ch[0]["t"]) > 0.5:
        warns.append("first chapter should be at 0 (YouTube requires it)")

    if plan.get("captions") and not words:
        warns.append("captions on but words_raw empty — captions will be skipped")

    return errs, warns


def main():
    plan = json.loads(Path(sys.argv[1]).read_text())
    errs, warns = check(plan)
    for e in errs:
        print(f"ERROR  {e}")
    for w in warns:
        print(f"warn   {w}")
    if errs:
        print(f"check: FAILED ({len(errs)} errors, {len(warns)} warnings)")
        sys.exit(1)
    print(f"check: ok ({len(warns)} warnings)")


def _selftest():
    import subprocess, tempfile
    d = Path(tempfile.mkdtemp())
    src = d / "s.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                    "-i", "color=c=gray:s=128x128:d=2:r=10", "-c:v", "libx264",
                    "-preset", "ultrafast", str(src)], check=True)
    base = {"source": str(src), "output": "f.mp4", "words_raw": [],
            "timeline": [{"name": "a", "clips": [[0, 1]]}]}
    good = {**base, "graphics": [{"template": "cta", "at": 0.2, "dur": 0.5}], "zooms": [],
            "chapters": [{"t": 0.0, "name": "Intro"}]}
    errs, _ = check(good)
    assert not errs, errs
    errs, _ = check({**good, "graphics": [{"template": "cta", "at": 0.2, "dur": 0.5},
                                          {"template": "cta", "at": 0.4, "dur": 0.5}]})
    assert any("overlaps" in e for e in errs), errs
    errs, _ = check({**good, "zooms": [{"at": 0.2, "dur": 1.0, "scale": 1.2, "cx": 0.5, "cy": 0.5},
                                       {"at": 0.8, "dur": 1.0, "scale": 1.2}]})
    assert any("compounds" in e or "overlaps" in e for e in errs), errs
    errs, _ = check({**good, "words_raw": [{"start": 0.4, "end": 0.8, "word": "hello"}],
                     "timeline": [{"name": "a", "clips": [[0.5, 1.0]]}]})
    assert any("inside word" in e for e in errs), errs
    errs, _ = check({**good, "graphics": [{"template": "nope", "at": 0.2, "dur": 0.5}]})
    assert any("unknown template" in e for e in errs), errs
    print("check.py selftest ok")


if __name__ == "__main__":
    (_selftest if "--selftest" in sys.argv else main)()
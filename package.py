#!/usr/bin/env python3
"""plan + rendered video -> upload package, written next to plan["output"]:

  thumbnail.png   frame from the hook moment + hook text (1280x720)
  captions.srt    soft captions for the YouTube upload (burn-ins stay in the MP4)
  description.txt title, first sentences verbatim, chapters, credit
  titles.txt      ranked title options from the hook candidates
  chapters.txt    YouTube chapter marks
"""
import json, subprocess, sys
from pathlib import Path

import gfx, render, shorts, autodress


def thumb_time(plan, words):
    """Where to grab the thumbnail frame: the best hook that survived the cut."""
    if t := plan.get("thumbnail_t"):
        return float(t)
    for c in plan.get("hook_candidates", []):
        t = autodress.source_to_output(plan, c["at"])
        if t is not None and 0.5 < t < render.total_dur(plan["timeline"]) - 2:
            return round(max(0.0, t - 0.4), 2)
    return round(min(2.0, render.total_dur(plan["timeline"]) / 4), 2)


def description(plan, words):
    out = [plan.get("title", "Untitled")]
    text = " ".join(w["word"] for w in words)
    sents, cur = [], ""
    for part in text.split(". "):
        cur = (cur + " " + part).strip()
        if part.endswith((".", "?", "!")):
            sents.append(cur); cur = ""
        if len(sents) == 2:
            break
    if cur and len(sents) < 2:
        sents.append(cur)
    if sents:
        body = " ".join(s.strip(".") for s in sents[:2]) + "."
        body = autodress.trim(body, 280)                      # tiny models barely punctuate
        out += ["", body]
    if ch := render.chapters_txt(plan):
        out += ["", "Chapters:", ch]
    out += ["", "Edited locally with vidpipe: https://github.com/dilates/vidpipe"]
    return "\n".join(out)


def main():
    plan = json.loads(Path(sys.argv[1]).read_text())
    work = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(sys.argv[1]).parent / "work"
    out = Path(plan["output"]).parent
    words = render.remap(plan.get("words_raw", []), plan["timeline"])
    accent = plan.get("accent", "#3ddc97")

    # thumbnail: real frame from the clean cut + hook text, 1280x720
    src = shorts.clean_cut(plan, work)
    t = thumb_time(plan, words)
    frame = work / "thumb_frame.png"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-ss", f"{t:.2f}", "-i", str(src), "-frames:v", "1", str(frame)],
                   check=True)
    overlay = gfx.render("thumb", plan.get("thumbnail", {}), work / "gfx",
                         accent=accent, w=1280, h=720)
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(frame),
                    "-i", str(overlay), "-filter_complex",
                    "[0:v]scale=1280:720:flags=lanczos,eq=contrast=1.05[b];"
                    "[b][1:v]overlay=0:0,format=yuv420p", "-frames:v", "1",
                    str(out / "thumbnail.png")], check=True)

    render.srt(words, out / "captions.srt")
    (out / "description.txt").write_text(description(plan, words) + "\n")
    (out / "chapters.txt").write_text((render.chapters_txt(plan) or "0:00 Full video") + "\n")

    titles = []
    if plan.get("title"):
        titles.append(plan["title"])
    for c in plan.get("hook_candidates", [])[:8]:
        cand = autodress.trim(autodress.hook_phrase({"hook_candidates": [c]}) or
                              c.get("text", "").split(".")[0], 60)
        if len(cand.split()) >= 4 and cand.lower() not in " | ".join(titles).lower():
            titles.append(cand)
    (out / "titles.txt").write_text(
        "\n".join(f"{i:02d}. {t}" for i, t in enumerate(titles, 1)) + "\n")
    print(f"-> package: {out}/ (thumbnail.png, captions.srt, description.txt, "
          f"titles.txt, chapters.txt)  thumb frame at {t:.1f}s")


if __name__ == "__main__":
    main()
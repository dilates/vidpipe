#!/usr/bin/env python3
"""Cut 9:16 shorts out of an edited timeline.

Works from the clean per-clip intermediates (the finished cut, before 16:9 graphics
are burned in), so the vertical crop never has to fight a widescreen overlay.
"""
import json, subprocess, sys
from pathlib import Path

import gfx, render, enc

HOOKY = ("insane", "crazy", "terrifying", "nobody", "most people", "never", "actually",
         "the trick", "turns out", "here's the thing", "you don't", "stop", "wrong",
         "truth", "real", "worst", "best", "why", "how", "what", "listen")
WEAK_START = ("and", "so", "um", "uh", "but", "then", "also", "which", "that", "it",
              "because", "or", "you know", "i mean")


# ---------------- segment selection ----------------
def sentences(words, gap=0.6):
    out, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1]["start"] if i + 1 < len(words) else 1e9
        if w["word"].endswith((".", "?", "!")) or nxt - w["end"] > gap:
            out.append(cur); cur = []
    if cur:
        out.append(cur)
    return [s for s in out if s]


def score(seg):
    txt = " ".join(w["word"] for w in seg).lower()
    dur = seg[-1]["end"] - seg[0]["start"]
    if dur <= 0:
        return -1
    first = " ".join(w["word"] for w in seg[:6]).lower().strip()
    s = 0.0
    s += sum(txt.count(k) for k in HOOKY) * 0.9
    s += min(len(seg) / dur / 3.2, 1.0) * 1.4                  # words per second
    if first.split() and first.split()[0].strip(".,") in WEAK_START:
        s -= 1.6                                                # opens mid-thought
    if seg[-1]["word"].endswith((".", "!", "?")):
        s += 0.8                                                # lands a full stop
    s += 1.0 if 24 <= dur <= 48 else (-1.2 if dur > 55 or dur < 18 else 0)
    return s


def candidates(words, want=5, lo=20.0, hi=52.0, avoid_tail=25.0):
    sents = sentences(words)
    end_limit = words[-1]["end"] - avoid_tail
    cands = []
    for i in range(len(sents)):
        seg = []
        for j in range(i, len(sents)):
            seg = seg + sents[j]
            dur = seg[-1]["end"] - seg[0]["start"]
            if dur < lo:
                continue
            if dur > hi:
                break
            if seg[-1]["end"] > end_limit:
                break
            cands.append({"start": round(seg[0]["start"], 2), "end": round(seg[-1]["end"], 2),
                          "score": round(score(seg), 3),
                          "text": " ".join(w["word"] for w in seg)})
    cands.sort(key=lambda c: -c["score"])
    picked = []
    for c in cands:
        if all(c["start"] >= p["end"] + 5 or c["end"] <= p["start"] - 5 for p in picked):
            picked.append(c)
        if len(picked) >= want:
            break
    return sorted(picked, key=lambda c: c["start"])


# ---------------- captions ----------------
def ass_vertical(words, path, accent="#3ddc97", max_chars=18, max_words=3,
                 y=1500, y_lift=1120, lift=()):
    def bgr(h):
        h = h.lstrip("#"); return f"&H{h[4:6]}{h[2:4]}{h[0:2]}&".upper()

    chunks, cur = [], []
    for w in words:
        if cur and (sum(len(x["word"]) + 1 for x in cur) + len(w["word"]) > max_chars
                    or len(cur) >= max_words or w["start"] - cur[-1]["end"] > 0.65):
            chunks.append(cur); cur = []
        cur.append(w)
        if w["word"].endswith((".", "?", "!")):
            chunks.append(cur); cur = []
    if cur:
        chunks.append(cur)

    rows = []
    for ch in chunks:
        for i, w in enumerate(ch):
            end = min(ch[i + 1]["start"] if i + 1 < len(ch) else w["end"] + 0.14, w["end"] + 0.55)
            if end <= w["start"]:
                continue
            txt = " ".join((f"{{\\c{bgr(accent)}}}{x['word'].upper()}{{\\c&HFFFFFF&}}" if j == i
                            else x["word"].upper()) for j, x in enumerate(ch))
            rows.append([w["start"], end, txt])
    rows.sort(key=lambda r: r[0])
    for a, b in zip(rows, rows[1:]):
        a[1] = min(a[1], b[0] - 0.001)

    def ts(t):
        h, r = divmod(max(0.0, t), 3600); m, s = divmod(r, 60)
        return f"{int(h)}:{int(m):02d}:{s:05.2f}"

    # Default sits low, over the dark desk, clear of the speaker. The info card
    # lives down there too, so lift the captions while it is on screen.
    ev = [f"Dialogue: 0,{ts(a)},{ts(b)},S,,0,0,0,,"
          f"{{\\pos(540,{y_lift if any(la <= a <= lb for la, lb in lift) else y})}}{t}"
          for a, b, t in rows if b > a]
    Path(path).write_text(
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 2\n"
        "ScaledBorderAndShadow: yes\n\n[V4+ Styles]\nFormat: Name,Fontname,Fontsize,PrimaryColour,"
        "SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,"
        "Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n"
        "Style: S,Fira Sans,86,&H00FFFFFF,&H00FFFFFF,&H00101010,&HB4000000,"
        "-1,0,0,0,100,100,1,0,1,6,4,5,60,60,60,1\n\n[Events]\nFormat: Layer,Start,End,Style,Name,"
        "MarginL,MarginR,MarginV,Effect,Text\n" + "\n".join(ev) + "\n")
    return len(ev)


# ---------------- render ----------------
def build(src, start, end, out, words, work, title=None, card=None,
          subject_cx=0.5, accent="#3ddc97", src_w=1920, src_h=1080):
    """src is the clean edited concat; start/end are on that timeline."""
    work = Path(work); work.mkdir(parents=True, exist_ok=True)
    cw = (int(src_h * 9 / 16) // 2) * 2
    x = int(min(max(subject_cx * src_w - cw / 2, 0), src_w - cw))

    seg = [{"start": round(w["start"] - start, 3), "end": round(w["end"] - start, 3),
            "word": w["word"]} for w in words if w["start"] >= start and w["end"] <= end]
    dur = end - start
    card_at = max(0.25, dur * 0.45)
    subs = work / f"{Path(out).stem}.ass"
    ass_vertical(seg, subs, accent,
                 lift=[(card_at - 0.4, card_at + 5.4)] if card else [])
    fc = [f"[0:v]crop={cw}:{src_h}:{x}:0,scale=1080:1920:flags=lanczos,"
          f"eq=contrast=1.04:saturation=1.06[v0]"]
    v, inputs, idx = "v0", [], 1
    for spec, tpl, at, d in ((title, "s_title", 0.25, 4.2), (card, "s_card", None, 5.0)):
        if not spec:
            continue
        at = at if at is not None else card_at
        png = gfx.render(tpl, spec, work / "gfx", accent=accent, w=1080, h=1920)
        inputs += ["-loop", "1", "-t", f"{d:.2f}", "-i", str(png)]
        fc.append(f"[{idx}:v]format=rgba,fade=t=in:st=0:d=0.3:alpha=1,"
                  f"fade=t=out:st={d - 0.35:.2f}:d=0.35:alpha=1,setpts=PTS+{at}/TB[g{idx}]")
        fc.append(f"[{v}][g{idx}]overlay=0:0:eof_action=pass:repeatlast=0:"
                  f"enable='between(t,{at},{at + d})'[vg{idx}]")
        v = f"vg{idx}"; idx += 1
    fc.append(f"[{v}]ass={subs},format=yuv420p[vout]")
    fc.append("[0:a]loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000,"
              "afade=t=in:d=0.02,afade=t=out:st=%.2f:d=0.25[aout]" % max(0.0, dur - 0.25))

    script = work / f"{Path(out).stem}.filter"
    script.write_text(";\n".join(fc))
    cmd = (["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(src)] + inputs +
           *render.fc_arg(script), "-map", "[vout]", "-map", "[aout]",
            *enc.video_args(quality=19, maxrate="20M", bufsize="40M"),
            "-c:a", "aac", "-b:a", "256k", "-ar", "48000", "-movflags", "+faststart", str(out)])
    subprocess.run(cmd, check=True)
    return out


# ---------------- clean cut ----------------
def clean_cut(plan, work):
    """The finished cut before 16:9 graphics burn in: per-clip intermediates -> one file.

    Reuses render's cut pass (resume-friendly); runs it when missing, so shorts and
    package work even if render hasn't run yet.
    """
    work = Path(work); work.mkdir(parents=True, exist_ok=True)
    clean = work / "clean.mp4"
    files = [work / f"clip{i:04d}.mp4" for i in range(len(render.flatten(plan["timeline"])))]
    if not all(f.exists() and f.stat().st_size > 0 for f in files):
        _, _, fps = render.probe(plan["source"])
        cmd, files = render.cut_pass(plan, work, fps)
        subprocess.run(cmd, check=True)
    lst = work / "clean.txt"
    lst.write_text("".join(f"file '{f}'\n" for f in files))
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-safe", "0",
                    "-f", "concat", "-i", str(lst), "-c", "copy", str(clean)],
                   check=True, capture_output=True)
    return clean


def _slug(text, n=4):
    words = ["".join(ch for ch in w.lower() if ch.isalnum()) for w in text.split()][:n]
    return "-".join(w for w in words if w)[:48] or "short"


def _title(text, n=44):
    t = " ".join(text.split())
    if len(t) <= n:
        return t
    return t[:n].rsplit(" ", 1)[0].rstrip(",;:.!?") + "…"


def main():
    import argparse
    ap = argparse.ArgumentParser(description="cut vertical shorts from a vidpipe plan")
    ap.add_argument("plan")
    ap.add_argument("work", nargs="?", help="cut-intermediates dir (default: <plan dir>/work)")
    ap.add_argument("-n", type=int, default=5)
    ap.add_argument("--subject-cx", type=float, default=None,
                    help="0..1 horizontal crop centre (default: plan's subject_cx, else 0.5)")
    a = ap.parse_args()

    plan = json.loads(Path(a.plan).read_text())
    work = Path(a.work) if a.work else Path(a.plan).parent / "work"
    src = clean_cut(plan, work)
    words = render.remap(plan.get("words_raw", []), plan["timeline"])
    total = render.total_dur(plan["timeline"])
    W, H, _ = render.probe(plan["source"])
    cx = a.subject_cx if a.subject_cx is not None else plan.get("subject_cx", 0.5)
    accent = plan.get("accent", "#3ddc97")
    out_dir = Path(plan["output"]).parent / "shorts"
    out_dir.mkdir(parents=True, exist_ok=True)

    picks = candidates(words, want=a.n)
    if not picks:
        raise SystemExit("shorts.py: no 20-52s self-contained segments found")
    lines = []
    for i, c in enumerate(picks, 1):
        out = out_dir / f"{i:02d}-{_slug(c['text'])}.mp4"
        title = {"kicker": "FROM THE FULL VIDEO", "title": _title(c["text"].split(".")[0])}
        print(f"[{i}/{len(picks)}] {c['end'] - c['start']:.0f}s  {out.name}")
        build(src, c["start"], c["end"], out, words, work / f"short{i}",
              title=title, subject_cx=cx, accent=accent, src_w=W, src_h=H)
        lines.append(f"{out.name}  {c['start']:.0f}s-{c['end']:.0f}s  score {c['score']}\n"
                     f"    {c['text'][:110]}")
    Path(plan["output"]).parent.joinpath("shorts.txt").write_text("\n".join(lines) + "\n")
    print(f"-> {len(picks)} shorts in {out_dir}")


def _selftest():
    w = [{"start": i * 0.4, "end": i * 0.4 + 0.35, "word": x} for i, x in
         enumerate(("this is actually insane. and so um it goes on. "
                    "most people never check this properly.").split())]
    ss = sentences(w)
    assert len(ss) == 3, [len(x) for x in ss]
    assert score(ss[0]) > score(ss[1]), "weak 'and so um' opener must rank lower"
    import tempfile
    d = Path(tempfile.mkdtemp())
    n = ass_vertical(w, d / "s.ass")
    body = (d / "s.ass").read_text()
    assert n and "PlayResX: 1080" in body and "\\pos(540," in body
    assert "THIS" in body, "shorts captions are upper-case"
    ev = [l for l in body.splitlines() if l.startswith("Dialogue")]
    def sec(t):
        h, m, s = t.split(":"); return int(h) * 3600 + int(m) * 60 + float(s)
    times = sorted((sec(l.split(",")[1]), sec(l.split(",")[2])) for l in ev)
    assert not [1 for a, b in zip(times, times[1:]) if a[1] > b[0] + 1e-6], "overlapping cues"
    print("shorts.py selftest ok")


if __name__ == "__main__":
    (_selftest if "--selftest" in sys.argv else main)()

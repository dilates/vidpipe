#!/usr/bin/env python3
"""plan.json -> finished MP4.

Pass 1 (only when the timeline reorders): one decode, N parallel outputs, one per
section, near-lossless. Feeding N `select` branches into `concat` in a single graph
deadlocks — concat drains branch 1 to EOF while the rest backpressure the split.
Independent output muxers all drain in parallel, so this is the safe shape.

Pass 2: concat demuxer -> zooms, graphics, captions, audio, SFX, music -> NVENC.
"""
import math
import json, subprocess, sys
from pathlib import Path

import gfx, sfx, enc

ROOT = Path(__file__).parent
CAP_STYLE = ("Style: Cap,Fira Sans,58,&H00FFFFFF,&H00FFFFFF,&H00101010,&HA0000000,"
             "-1,0,0,0,100,100,0.6,0,1,4.5,2.5,2,90,90,105,1")


# ---------- expressions ----------
def _ss(x):
    return f"({x})*({x})*(3-2*({x}))"


def _bump(t0, dur, ramp=0.5):
    return f"({_ss(f'clip((t-{t0})/{ramp},0,1)')})*({_ss(f'clip(({t0}+{dur}-t)/{ramp},0,1)')})"


def zoom_exprs(zooms):
    if not zooms:
        return None
    z, cx, cy = ["1"], ["0.5"], ["0.5"]
    for k in zooms:
        amt = k.get("scale", 1.25) - 1
        b = _bump(k["at"], k.get("dur", 3.0), k.get("ramp", 0.5))
        z.append(f"{amt}*{b}")
        cx.append(f"({k.get('cx', 0.5) - 0.5})*{b}")
        cy.append(f"({k.get('cy', 0.5) - 0.5})*{b}")
    return "+".join(z), "+".join(cx), "+".join(cy)


def crop_offsets(zf, W, H):
    """Scaled dimensions as literal expressions, so crop can compute its own offset
    instead of trusting iw/ih (which crop binds once, before any eval=frame scale)."""
    return f"trunc({W}*({zf})/2)*2", f"trunc({H}*({zf})/2)*2"


def anim_pos(anim, t0, d=0.42, dist=64):
    slide = f"{dist}*(1-{_ss(f'clip((t-{t0})/{d},0,1)')})"
    return {"slide_left": (slide, "0"), "slide_right": (f"-{slide}", "0"),
            "slide_up": ("0", slide), "slide_down": ("0", f"-{slide}"),
            "fade": ("0", "0")}.get(anim, ("0", "0"))


# ---------- timeline ----------
def flatten(timeline):
    return [c for sec in timeline for c in sec["clips"]]


def section_dur(sec):
    return sum(e - s for s, e in sec["clips"]) / float(sec.get("speed", 1.0) or 1.0)


def total_dur(timeline):
    return sum(section_dur(s) for s in timeline)


def remap(words, timeline):
    """Words onto the OUTPUT timeline, honouring section order and speed."""
    out, base = [], 0.0
    for sec in timeline:
        sp = float(sec.get("speed", 1.0) or 1.0)
        for s, e in sec["clips"]:
            for w in words:
                if w["start"] >= s and w["end"] <= e:
                    out.append({"start": round((w["start"] - s) / sp + base, 3),
                                "end": round((w["end"] - s) / sp + base, 3),
                                "word": w["word"]})
            base += (e - s) / sp
    return out


# ---------- captions ----------
def _ts(t):
    h, r = divmod(max(0.0, t), 3600)
    m, s = divmod(r, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def caption_chunks(words, max_chars=32, max_words=5, gap=0.7):
    chunks, cur = [], []
    for w in words:
        too_long = sum(len(x["word"]) + 1 for x in cur) + len(w["word"]) > max_chars
        broke = cur and w["start"] - cur[-1]["end"] > gap
        if cur and (too_long or broke or len(cur) >= max_words):
            chunks.append(cur); cur = []
        cur.append(w)
        if w["word"].endswith((".", "?", "!")):
            chunks.append(cur); cur = []
    if cur:
        chunks.append(cur)
    return chunks


def srt(words, path):
    """Soft captions for YouTube upload (burned-in ones come from ass())."""
    def st(t):
        h, r = divmod(max(0.0, t), 3600); m, s = divmod(r, 60)
        return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{round((s % 1) * 1000):03d}"

    rows = [[ch[0]["start"], ch[-1]["end"] + 0.12, " ".join(w["word"] for w in ch)]
            for ch in caption_chunks(words)]
    rows.sort(key=lambda r: r[0])
    for a, b in zip(rows, rows[1:]):                     # a chunk's tail must not
        a[1] = min(a[1], b[0] - 0.001)                   # run into the next cue
    rows = [r for r in rows if r[1] > r[0]]
    Path(path).write_text("\n\n".join(
        f"{i}\n{st(a)} --> {st(b)}\n{t}" for i, (a, b, t) in enumerate(rows, 1)) + "\n")
    return len(rows)


# Templates anchored to the bottom of the frame; captions must move up over these.
BOTTOM_TEMPLATES = {"lower_third", "cta"}
CAP_LIFT_Y = 742          # bottom-centre anchor, clear of a lower third's top edge


def ass(words, path, accent="#3ddc97", max_chars=32, max_words=5, gap=0.7, lift=()):
    def bgr(h):
        h = h.lstrip("#")
        return f"&H{h[4:6]}{h[2:4]}{h[0:2]}&".upper()

    chunks = caption_chunks(words, max_chars, max_words, gap)
    rows = []
    for ch in chunks:
        for i, w in enumerate(ch):
            end = min(ch[i + 1]["start"] if i + 1 < len(ch) else w["end"] + 0.12, w["end"] + 0.6)
            if end <= w["start"]:
                continue
            txt = " ".join((f"{{\\c{bgr(accent)}}}{x['word']}{{\\c&HFFFFFF&}}" if j == i
                            else x["word"]) for j, x in enumerate(ch))
            rows.append([w["start"], end, txt, i == 0])

    # A chunk's last word carries a 0.12s tail that can run into the next chunk's
    # first word, putting two caption lines on screen at once. Clamp to the successor.
    rows.sort(key=lambda r: r[0])
    for a, b in zip(rows, rows[1:]):
        a[1] = min(a[1], b[0] - 0.001)

    ev = []
    for st, en, txt, first in rows:
        if en <= st:
            continue
        up = any(a <= st <= b for a, b in lift)
        tags = ("{\\pos(960,%d)}" % CAP_LIFT_Y if up else "") + ("{\\fad(70,0)}" if first else "")
        ev.append(f"Dialogue: 0,{_ts(st)},{_ts(en)},Cap,,0,0,0,,{tags}{txt}")

    Path(path).write_text(
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\nWrapStyle: 2\n"
        "ScaledBorderAndShadow: yes\n\n[V4+ Styles]\nFormat: Name,Fontname,Fontsize,PrimaryColour,"
        "SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,"
        "Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n"
        + CAP_STYLE + "\n\n[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,"
        "Effect,Text\n" + "\n".join(ev) + "\n")
    return len(ev)


# ---------- sfx ----------
def sfx_events(plan):
    """`sfx: "auto"` -> a whoosh on every graphic, an impact on every section cut."""
    spec = plan.get("sfx")
    if isinstance(spec, list):
        return spec
    if spec != "auto":
        return []
    ev, t = [], 0.0
    for i, sec in enumerate(plan["timeline"]):
        if i:
            ev.append({"t": round(t, 3), "kind": "impact", "gain": 0.55})
        t += section_dur(sec)
    for g in plan.get("graphics", []):
        kind = {"title": "riser", "endscreen": "riser"}.get(g["template"], "whoosh")
        lead = 0.9 if kind == "riser" else 0.12
        ev.append({"t": round(max(0.0, float(g["at"]) - lead), 3), "kind": kind,
                   "gain": 0.5 if kind == "whoosh" else 0.42})
    return ev


# ---------- ffmpeg ----------
def probe(src):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height,r_frame_rate", "-of", "json", src],
                       capture_output=True, text=True, check=True)
    s = json.loads(r.stdout)["streams"][0]
    n, d = s["r_frame_rate"].split("/")
    return s["width"], s["height"], round(int(n) / int(d), 3)


def _sel(clips):
    return "+".join(f"between(t,{s},{e})" for s, e in clips)


def _atempo(sp):
    """atempo is capped at 2x per instance; chain for anything faster."""
    out = []
    while sp > 2.0:
        out.append("atempo=2.0"); sp /= 2.0
    while sp < 0.5:
        out.append("atempo=0.5"); sp *= 2.0
    if abs(sp - 1.0) > 1e-3:
        out.append(f"atempo={sp:.5f}")
    return out


# ffmpeg 9 removed -filter_complex_script; "-/filter_complex <file>" is the generic
# "read this option's value from a file" form that replaced it.
def cut_pass(plan, work, fps, declick=0.025):
    """One decode -> one near-lossless file per CLIP.

    Per clip rather than per section so every cut edge gets a short audio ramp.
    A hard splice in a waveform clicks; the ramp removes it without moving any
    timing, which a crossfade would (it overlaps, shortening the result).
    """
    clips = [(i, c, float(sec.get("speed", 1.0) or 1.0))
             for i, sec in enumerate(plan["timeline"]) for c in sec["clips"]]
    fc, cmd_out, files = [], [], []
    fc.append(f"[0:v]split={len(clips)}" + "".join(f"[s{i}]" for i in range(len(clips))))
    fc.append(f"[0:a]asplit={len(clips)}" + "".join(f"[as{i}]" for i in range(len(clips))))
    for i, (_, (cs, ce), sp) in enumerate(clips):
        dur = (ce - cs) / sp
        d = min(declick, max(0.004, dur / 4))            # keep ramps inside tiny clips
        fc.append(f"[s{i}]select='between(t,{cs},{ce})',setpts=N/FRAME_RATE/TB/{sp}[v{i}]")
        ach = ",".join(["asetpts=N/SR/TB"] + _atempo(sp) +
                       [f"afade=t=in:st=0:d={d:.4f}",
                        f"afade=t=out:st={max(0.0, dur - d):.4f}:d={d:.4f}"])
        fc.append(f"[as{i}]aselect='between(t,{cs},{ce})',{ach}[a{i}]")
        f = work / f"clip{i:04d}.mp4"
        files.append(f)
        # libx264, not NVENC: consumer cards cap concurrent encode sessions and this
        # pass opens one per clip. crf 14 ultrafast is visually transparent here.
        cmd_out += ["-map", f"[v{i}]", "-map", f"[a{i}]",
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "14",
                    "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", "-f", "mov", str(f)]
    script = work / "cut.txt"
    script.write_text(";\n".join(fc))
    return (["ffmpeg", "-y", "-hide_banner", "-i", plan["source"],
             "-/filter_complex", str(script)] + cmd_out), files


def dress_pass(plan, work, vin, W, H, fps, total, single=None):
    """Graphics, captions, zooms, audio, SFX, music -> final encode."""
    accent = plan.get("accent", "#3ddc97")
    fc, inputs, idx = [], [], 1

    if single:
        fc.append(f"[0:v]select='{_sel(single)}',setpts=N/FRAME_RATE/TB[v0]")
        fc.append(f"[0:a]aselect='{_sel(single)}',asetpts=N/SR/TB[araw]")
        v, araw = "v0", "araw"
    else:
        fc.append("[0:v]null[v0]"); fc.append("[0:a]anull[araw]")
        v, araw = "v0", "araw"

    if z := zoom_exprs(plan.get("zooms")):
        zf, cx, cy = z
        # crop binds iw/ih at config time, so with an eval=frame scale in front they
        # stay at the ORIGINAL size and (iw-W) is always 0 -> the punch-in crops the
        # top-left corner. Re-derive the scaled size from the same zoom expression.
        sw, sh = crop_offsets(zf, W, H)
        fc.append(f"[{v}]scale=w='{sw}':h='{sh}':eval=frame:flags=lanczos,"
                  f"crop={W}:{H}:x='({sw}-{W})*({cx})':y='({sh}-{H})*({cy})'[vz]")
        v = "vz"

    for i, g in enumerate(plan.get("graphics", [])):
        png = gfx.render(g["template"], g.get("vars", {}), work / "gfx",
                         accent=g.get("accent", accent))
        t0, d = float(g["at"]), float(g.get("dur", 4.0))
        inputs += ["-loop", "1", "-t", f"{d:.3f}", "-i", str(png)]
        fi, fo = g.get("fade_in", 0.35), g.get("fade_out", 0.35)
        fc.append(f"[{idx}:v]format=rgba,fade=t=in:st=0:d={fi}:alpha=1,"
                  f"fade=t=out:st={max(0, d - fo):.3f}:d={fo}:alpha=1,setpts=PTS+{t0}/TB[g{i}]")
        x, y = anim_pos(g.get("anim", "slide_left"), t0)
        fc.append(f"[{v}][g{i}]overlay=x='{x}':y='{y}':eof_action=pass:repeatlast=0:"
                  f"enable='between(t,{t0},{t0 + d})'[vg{i}]")
        v = f"vg{i}"; idx += 1

    words = remap(plan.get("words_raw", []), plan["timeline"]) or plan.get("transcript", [])
    if plan.get("captions") and words:
        subs = work / "captions.ass"
        lift = [(g["at"] - 0.3, g["at"] + float(g.get("dur", 4.0)) + 0.3)
                for g in plan.get("graphics", []) if g["template"] in BOTTOM_TEMPLATES]
        if ass(words, subs, accent, lift=lift):
            fc.append(f"[{v}]ass={subs}[vs]")   # Fira Sans resolves via fontconfig
            v = "vs"
    fc.append(f"[{v}]format=yuv420p[vout]")

    # speech: clean up, then leave headroom for SFX/music. -14.3 here lands the
    # finished mix near -14 LUFS; targeting -16 measured -15.7 final, i.e. too quiet.
    # Measured: the SFX mix + limiter pull ~0.9 LU off the speech target, so -14.3
    # here landed at -15.2 LUFS integrated. -13.1 lands at -14.4, which is what
    # YouTube normalises to. Re-measure with ebur128 if the mix changes.
    fc.append(f"[{araw}]highpass=f=75,afftdn=nr=10:nf=-30,"
              f"acompressor=threshold=-20dB:ratio=3:attack=8:release=180,"
              f"loudnorm=I={plan.get('speech_lufs', -13.1)}:TP=-2.0:LRA=11,aresample=48000[sp]")
    mix = ["[sp]"]

    if ev := sfx_events(plan):
        bed = sfx.bed(ev, total + 2, work / "sfx.wav")
        inputs += ["-i", str(bed)]
        fc.append(f"[{idx}:a]volume={plan.get('sfx_db', -13)}dB[sx]")
        mix.append("[sx]"); idx += 1

    if m := plan.get("music"):
        inputs += ["-stream_loop", "-1", "-i", m]
        fc.append(f"[{idx}:a]volume={plan.get('music_db', -22)}dB,aformat=sample_fmts=fltp,"
                  f"aresample=48000[mu]")
        fc.append("[mu][sp]sidechaincompress=threshold=0.03:ratio=8:attack=15:release=400[mud]")
        mix.append("[mud]"); idx += 1

    if len(mix) > 1:
        # 0.85 ~= -1.4 dBFS sample peak, leaving room for inter-sample peaks that AAC
        # pushes higher. level=disabled is required: alimiter's auto-level defaults ON
        # and renormalises straight back to 0 dB, which silently undoes the limit.
        fc.append(f"{''.join(mix)}amix=inputs={len(mix)}:duration=first:normalize=0,"
                  f"alimiter=limit=0.85:level=disabled[aout]")
    else:
        fc.append("[sp]anull[aout]")

    script = work / "dress.txt"
    script.write_text(";\n".join(fc))
    return (["ffmpeg", "-y", "-hide_banner"] + vin + inputs +
            ["-/filter_complex", str(script), "-map", "[vout]", "-map", "[aout]",
             # spatial+temporal AQ matter most on a locked-off shot: without them NVENC
             # spends its bits on the static background and softens the moving speaker.
             # Measured vs the near-lossless intermediate: +3.2 dB PSNR over p6/cq21.
             *enc.video_args(quality=17),
             "-c:a", "aac", "-b:a", "320k", "-ar", "48000", "-movflags", "+faststart",
             plan["output"]])


def chapters_txt(plan):
    out = []
    for c in plan.get("chapters", []):
        t = int(c["t"]); h, r = divmod(t, 3600); m, s = divmod(r, 60)
        out.append((f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}") + f" {c['name']}")
    return "\n".join(out)


def main():
    plan = json.loads(Path(sys.argv[1]).read_text())
    work = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(sys.argv[1]).parent / "work"
    work.mkdir(parents=True, exist_ok=True)
    Path(plan["output"]).parent.mkdir(parents=True, exist_ok=True)
    W, H, fps = probe(plan["source"])
    tl = plan["timeline"]
    total = total_dur(tl)

    if len(tl) == 1 and abs(float(tl[0].get("speed", 1.0) or 1.0) - 1.0) < 1e-3:
        vin, single = ["-i", plan["source"]], tl[0]["clips"]      # no reorder: one pass
    else:
        cmd, files = cut_pass(plan, work, fps)
        lst = work / "concat.txt"
        if all(f.exists() and f.stat().st_size > 0 for f in files):
            print(f"[1/2] reusing {len(files)} cut sections")     # resume after a dress-only change
        else:
            print(f"[1/2] cutting {len(files)} sections…")
            subprocess.run(cmd, check=True)
        lst.write_text("".join(f"file '{f}'\n" for f in files))
        vin, single = ["-safe", "0", "-f", "concat", "-i", str(lst)], None
        print("[2/2] dressing…")

    if ch := chapters_txt(plan):
        Path(plan["output"]).with_suffix(".chapters.txt").write_text(ch + "\n")
    subprocess.run(dress_pass(plan, work, vin, W, H, fps, total, single), check=True)
    print(f"-> {plan['output']}  ({total:.0f}s)")


def _selftest():
    import tempfile
    assert _ts(3661.5) == "1:01:01.50"
    prod = lambda sp: __import__("math").prod(float(x.split("=")[1]) for x in _atempo(sp))
    assert _atempo(1.0) == []
    for sp in (0.25, 0.5, 2.0, 4.0, 3.0):
        assert abs(prod(sp) - sp) < 1e-4, (sp, _atempo(sp))
    tl = [{"name": "hook", "clips": [[50, 58]], "speed": 1.0},
          {"name": "body", "clips": [[0, 10], [12, 20]], "speed": 2.0}]
    assert flatten(tl) == [[50, 58], [0, 10], [12, 20]]
    assert abs(total_dur(tl) - (8 + 9)) < 1e-6
    w = [{"start": 52.0, "end": 52.5, "word": "insane"},   # hook clip [50,58]
         {"start": 4.0, "end": 4.4, "word": "so"},          # body clip 1 [0,10]
         {"start": 13.0, "end": 13.4, "word": "anyway"}]    # body clip 2 [12,20]
    r = remap(w, tl)
    assert r[0]["start"] == 2.0, r          # 2s into an unsped hook at t=0
    assert r[1]["start"] == 10.0, r         # 8s hook + 4s/2x
    assert r[2]["start"] == 13.5, r         # 8s hook + clip1 (10s/2x) + 1s/2x
    assert r[2]["end"] == 13.7, r           # 0.4s word plays in 0.2s at 2x
    d = Path(tempfile.mkdtemp())
    n = ass([{"start": 0.0, "end": 0.4, "word": "hello"},
             {"start": 0.4, "end": 0.9, "word": "world."}], d / "c.ass")
    assert n == 2 and "\\c&H97DC3D&" in (d / "c.ass").read_text()
    n = srt([{"start": 0.0, "end": 0.4, "word": "hello"},
             {"start": 0.4, "end": 0.9, "word": "world."}], d / "c.srt")
    assert n == 1 and "00:00:00,000 --> " in (d / "c.srt").read_text()
    ev = sfx_events({"timeline": tl, "sfx": "auto",
                     "graphics": [{"template": "title", "at": 9.0}]})
    kinds = sorted(e["kind"] for e in ev)
    assert kinds == ["impact", "riser"], kinds        # section cut + title riser
    assert abs(next(e["t"] for e in ev if e["kind"] == "impact") - 8.0) < 1e-6
    # zoom_exprs already returns the centre as a 0..1 fraction; the crop must NOT
    # add another 0.5 or the punch-in flies to the corner instead of the subject.
    zf, cx, cy = zoom_exprs([{"at": 10, "dur": 5, "scale": 1.2, "cx": 0.5, "cy": 0.42}])
    def _ev(e, t):
        return eval(e.replace("clip(", "min(max(").replace(",0,1)", ",0),1)"),
                    {"t": t, "min": min, "max": max, "trunc": math.trunc})
    assert abs(_ev(zf, 12.5) - 1.2) < 1e-6
    assert abs(_ev(cx, 12.5) - 0.5) < 1e-6, "centred zoom must stay centred"
    assert abs(_ev(cy, 12.5) - 0.42) < 1e-6
    assert abs(_ev(zf, 0.0) - 1.0) < 1e-6, "no zoom outside the window"
    # ...and the crop must actually land there. Checking the expressions alone let a
    # top-left punch-in ship: crop's iw was stuck at the pre-scale width, so x was 0.
    sw, sh = crop_offsets(zf, 1920, 1080)
    cropx = f"({sw}-1920)*({cx})"
    cropy = f"({sh}-1080)*({cy})"
    # The anchor (cx,cy) must stay at the SAME fraction of the frame through the
    # punch-in: source 0.5/0.42 lands at output 0.5/0.42, not drifting to a corner.
    px = _ev(sw, 12.5) * 0.5 - _ev(cropx, 12.5)
    py = _ev(sh, 12.5) * 0.42 - _ev(cropy, 12.5)
    assert abs(px - 0.50 * 1920) < 2, f"zoom must hold the subject's x, got {px}"
    assert abs(py - 0.42 * 1080) < 2, f"zoom must hold the subject's y, got {py}"
    assert abs(_ev(cropx, 0.0)) < 1e-6, "no crop offset outside the zoom window"
    print("render.py selftest ok")


if __name__ == "__main__":
    (_selftest if "--selftest" in sys.argv else main)()

#!/usr/bin/env python3
"""Generate a synthetic talking-head fixture: piper TTS + a moving subject box.

Output: tests/fixture/fixture.mp4 (~70s, 1080p25) — real speech with pauses, a
hook sentence, fillers and numbers, and a "speaker" that moves at ~35% frame
width so subject detection and shorts cropping have something honest to find.

Requires piper-tts, fetched on demand via `uv run` (the voice caches in
~/.cache/piper). Fully local; no network is used once the voice is cached.
"""
import os, subprocess, sys, wave
from pathlib import Path

import numpy as np

SR = 22050                                  # piper's native rate
HERE = Path(__file__).parent
OUT = HERE / "fixture"
VOICE = Path.home() / ".cache" / "piper" / "en_US-lessac-medium"

SENTENCES = [
    "Most people never check this setting, and it is insane how much time it saves.",
    "So today I want to show you the trick that changed my workflow completely.",
    "um, let me back up for a second.",
    "When I first started editing, I spent hours on every single video.",
    "Here's the thing. You don't need fancy software to cut a good video.",
    "The trick is to plan your cuts before you record anything.",
    "I tested this on ten videos last month.",
    "Editing time dropped from six hours to forty minutes. That is not a typo.",
    "Why does this work so well?",
    "Because a plan forces you to decide what actually matters.",
    "Now, most people record first and think later.",
    "That is exactly backwards, and it costs you hours every week.",
    "So, try it on your next recording.",
    "Write down five beats before you press record, and watch what happens.",
]


def ensure_voice():
    if VOICE.exists() and VOICE.with_suffix(".onnx.json").exists():
        return
    VOICE.parent.mkdir(parents=True, exist_ok=True)
    base = ("https://huggingface.co/rhasspy/piper-voices/resolve/main/"
            "en/en_US/lessac/medium/en_US-lessac-medium")
    for suffix in (".onnx", ".onnx.json"):
        subprocess.run(["curl", "-sL", "--retry", "3", "--retry-delay", "5",
                        "-o", str(VOICE) + suffix, base + suffix], check=True)


def speak():
    """All sentences in one piper call -> per-sentence wavs, in input order."""
    tmp = OUT / "tts"
    tmp.mkdir(parents=True, exist_ok=True)
    for f in tmp.glob("*.wav"):
        f.unlink()
    script = "\n".join(SENTENCES)
    r = subprocess.run(
        ["uv", "run", "--python", "3.12", "--with", "piper-tts",
         "python", "-m", "piper", "-m", str(VOICE), "--output-dir", str(tmp)],
        input=script, text=True, capture_output=True, check=True)
    wrote = [l.split()[-1] for l in r.stderr.splitlines() if "Wrote" in l]
    if not wrote:                                        # piper <1.3 logs to stdout
        wrote = [l.split()[-1] for l in r.stdout.splitlines() if "Wrote" in l]
    files = sorted((tmp / w) for w in wrote)
    assert len(files) == len(SENTENCES), (len(files), r.stderr[-400:])
    return files


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    ensure_voice()
    wavs = speak()

    # timeline: 0.6s lead-in, sentence, gap of 1.0-2.2s (deterministic)
    rng = np.random.default_rng(7)
    audio, t, marks = [], 0.0, []
    audio.append(np.zeros(int(0.6 * SR), np.float32)); t += 0.6
    for w in wavs:
        with wave.open(str(w)) as f:
            x = np.frombuffer(f.readframes(f.getnframes()), np.int16).astype(np.float32) / 32768
        marks.append((round(t, 3), round(t + len(x) / SR, 3)))
        audio.append(x)
        gap = float(rng.uniform(1.0, 2.2))
        audio.append(np.zeros(int(gap * SR), np.float32)); t += len(x) / SR + gap
    mix = np.concatenate(audio)
    mix /= max(1.0, np.abs(mix).max())
    raw = OUT / "speech.wav"
    with wave.open(str(raw), "wb") as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(SR)
        f.writeframes((mix * 32767).astype(np.int16).tobytes())

    dur = len(mix) / SR
    # the "speaker": a textured box bobbing at ~35% width; the room stays still.
    # Texture matters: a flat box only diffs at its edges after the 64x36
    # downsample, which skews the motion centroid. Periods 5.3/7.9s are
    # deliberately irrational vs detect_subject's sampling grids (12s, then 2s).
    # drawbox can't animate here (its expressions evaluate once), so the speaker
    # is a static PNG moved by overlay, whose x/y ARE per-frame.
    spk = OUT / "speaker.png"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                    "-i", "color=c=0x2f9e6f:s=520x780",
                    "-vf", "drawbox=x=80:y=170:w=90:h=90:color=0x11151b@0.9:t=fill,"
                           "drawbox=x=330:y=170:w=90:h=90:color=0x11151b@0.9:t=fill,"
                           "drawbox=x=130:y=470:w=260:h=52:color=0x0d1117@0.9:t=fill",
                    "-frames:v", "1", str(spk)], check=True)
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", f"color=c=0x151a21:s=1920x1080:r=25:d={dur:.2f}",
                    "-loop", "1", "-i", str(spk),
                    "-i", str(raw), "-filter_complex",
                    "[0:v][1:v]overlay=x='390+20*sin(2*PI*t/7.9)':"
                    "y='237.6+60*sin(2*PI*t/5.3)':shortest=1[v]",
                    "-map", "[v]", "-map", "2:a", "-ar", "48000", "-shortest",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-g", "50",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                    str(OUT / "fixture.mp4")], check=True)
    spk.unlink()
    raw.unlink()
    (OUT / "sentences.tsv").write_text(
        "\n".join(f"{a}\t{b}\t{s}" for (a, b), s in zip(marks, SENTENCES)) + "\n")
    print(f"fixture: {OUT / 'fixture.mp4'}  ({dur:.0f}s, {len(SENTENCES)} sentences)")


if __name__ == "__main__":
    main()
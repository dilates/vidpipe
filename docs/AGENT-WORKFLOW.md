# The editorial pass — the judgement part a script can't do

`run.sh auto` dresses a video with deterministic graphics, and that's the floor,
not the ceiling. The retained-viewer difference between a finished cut and an
edited one is structural: what gets cut, where sections move, what the screen
says while the speaker talks. That pass is a judgement call — this document is
the checklist. It works for an LLM agent (it was written for one) or a human
with a text editor: everything happens in `plan.json`, then `run.sh check` and
`run.sh render`.

**The script does the mechanical work. You do the editorial pass.** Never skip it.

## 0. Probe, and LOOK at it

`ffprobe` for duration/res/fps, then sample ~6 frames into a contact sheet and
view it. Talking head or gameplay? Static or moving camera? Where the subject
sits in frame decides which side graphics live on and where shorts crop.

Find the subject objectively (needed for shorts and zooms): sample greyscale
frames, take `abs(diff)` between them, collapse to columns. The speaker moves,
the room doesn't, so the motion centroid is the subject's x.
`autodress.detect_subject()` does this for you; **for gameplay, use 0.5**.

## 1. Prep

    ./run.sh prep RAW.mp4 talking        # or gameplay

## 2. Repair the transcript — ALWAYS

Whisper drops into repetition loops and invents text. Detect low 3-gram
diversity over a sliding window (< 0.72), re-transcribe each suspect region ±5s
with `--vad_filter False --condition_on_previous_text False`, splice into
`words_raw`, re-check. Still repeating after a clean re-transcription is real
speech (the speaker repeating for emphasis) — trim it editorially. Skim for
mis-heard product names and domains.

## 3. Read the whole transcript

Dump timestamped lines and read every one. Mark: flubs and retakes ("let me try
that again"), dead air, rambles and self-deprecation ("sorry this is long" —
always cut; never let the video tell the viewer it's boring), duplicate topics,
and the hook.

## 4. Restructure for retention

`timeline` sections play in list order and may jump backwards in the source.
Cold open (5–15s, don't replay it later) → title card → body → soft CTA around
40–50% → end screen with a comment prompt over the last ~15s.

**Reorder only where references survive.** Monologues back-reference
constantly; moving a section can strand a callback — or repair one, if you move
a story next to the line that refers to it. Check every seam.

### Cut quality — what users notice first

- **Never cut inside a word.** `plan.snap_clips()` enforces it; run it *last*,
  after section edges and drops, because those create boundaries the earlier
  passes never saw. `run.sh check` asserts zero.
- Snap section edges to a real pause (≥ 0.28s, search ±3s).
- Only cut pauses over ~0.8s. Cutting a 0.4s intra-sentence breath reads as
  "cut me off mid-sentence" even though no word was clipped.
- Keep a ~0.15s tail after the last word — whisper's end times clip consonants.
- Cut edges get a ~25ms audio ramp per clip (already built in). Use ramps, not
  crossfades: crossfades overlap and shift every downstream timing.
- Cover far source jumps with a `transition` card so a jump reads as a beat.

## 5. Graphics

Anchor to the **source** second where the point is made and convert to output
time — `autodress.source_to_output()` does the conversion. Roughly one per
30–50s. `lower_third callout stat compare code chapter checklist quote` are
explanatory; `title tease cta endscreen transition` are retention.

**Verify every on-screen claim.** Never contradict or over-claim past what the
speaker said — if they assert something absolute, put the verifiable fact on
screen instead. **Never embed images scraped from the web** into someone's
monetised video; build visuals from templates.

Punch-in zooms matter most on a locked-off shot: section starts + emphasis
beats, scale 1.15–1.25, `cx`/`cy` set to the detected subject.

## 6. Validate before rendering

    ./run.sh check plan.json

This catches the things that bite: graphics overlapping each other or running
past the end, **zooms overlapping** (zoom expressions *sum* — two at once
compound into a lurch), cuts inside words, chapters out of order.

## 7. Render

    ./run.sh render plan.json

Two passes; cut intermediates are reused, so dress-only changes skip re-cutting.

## 8. Verify — never trust exit code 0

- **Loudness ≈ −14 LUFS, true peak ≤ −1 dBTP** (`ebur128=peak=true`). Peak near
  0 means the limiter is renormalising.
- **Motion sharpness.** If the subject is soft while the background is fine, the
  encoder is starving motion. Measure SSIM/PSNR against the intermediate over
  the whole runtime, not one frame.
- Extract frames at graphic timestamps into a contact sheet and look.

## 9. Shorts

`run.sh shorts plan.json` scores 20–52s self-contained segments and renders
1080×1920 from the **clean master** (the finished cut before 16:9 graphics burn
in). The scorer is a starting point: it ranks openers that begin mid-thought
("and so um…") too highly. Read the candidates and pick clean in-points
yourself — a short lives or dies on its first second.

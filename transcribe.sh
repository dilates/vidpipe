#!/usr/bin/env bash
# transcribe.sh SOURCE OUTDIR [MODEL] -> OUTDIR/<name>.json (word-level timestamps)
#
# Uses whisper-ctranslate2 (faster-whisper CLI). GPU is used when available,
# CPU int8 otherwise. VIDPIPE_LANG sets the language (default: en).
set -euo pipefail
src="$1"; out="${2:-.}"; model="${3:-large-v3}"; lang="${VIDPIPE_LANG:-en}"
base="$(basename "${src%.*}")"

# Locate the CLI, plus the CUDA libs that arrive as pip wheels beside it
# (uv-tools layout). The wheel libs are needed even when the CLI is on PATH.
WHISPER="${WHISPER:-$(command -v whisper-ctranslate2 || true)}"
tools="$HOME/.local/share/uv/tools/whisper-ctranslate2"
if [ -d "$tools" ]; then
  libs=$(find "$tools" -type d -name lib -path '*nvidia*' 2>/dev/null | tr '\n' ':')
  export LD_LIBRARY_PATH="${libs}${LD_LIBRARY_PATH:-}"
  [ -n "$WHISPER" ] || WHISPER="$(find "$tools" -name whisper-ctranslate2 -type f -path '*/bin/*' | head -1)"
fi
[ -n "$WHISPER" ] || { echo "vidpipe: whisper-ctranslate2 not found — see README (Transcription)" >&2; exit 1; }

mkdir -p "$out"
final="$out/$base.json"
if [ -f "$final" ]; then echo "$final"; exit 0; fi      # resume: keep the transcript
wav="$out/$base.16k.wav"
[ -f "$wav" ] || ffmpeg -y -v error -i "$src" -vn -ac 1 -ar 16000 -c:a pcm_s16le "$wav"

common=(--model "$model" --language "$lang" --word_timestamps True --vad_filter True
        --output_format json --output_dir "$out" --verbose False)
want="$out/$base.16k.json"
# A failed CUDA init can still exit 0, so success is judged by the output file.
"$WHISPER" "$wav" --device cuda --compute_type float16 "${common[@]}" || true
if [ ! -f "$want" ]; then
  echo "cuda unavailable, transcribing on cpu" >&2
  "$WHISPER" "$wav" --device cpu --compute_type int8 "${common[@]}"
fi
[ -f "$want" ] || { echo "vidpipe: transcription failed" >&2; exit 1; }

mv -f "$out/$base.16k.json" "$out/$base.json"
rm -f "$wav"
echo "$out/$base.json"
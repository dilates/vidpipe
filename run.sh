#!/usr/bin/env bash
# vidpipe — raw footage to finished, ready-to-post video. 100% local.
#
#   ./run.sh auto    RAW.mp4 [gameplay|talking] [model]   one command, end to end
#   ./run.sh prep    RAW.mp4 [gameplay|talking] [model]   transcribe + cut plan -> <raw>.vidpipe/plan.json
#   ./run.sh check   PLAN.json                            validate a plan before rendering
#   ./run.sh render  PLAN.json                            final MP4 (+cut intermediates, resumable)
#   ./run.sh shorts  PLAN.json [-n 5]                     vertical shorts from the clean cut
#   ./run.sh package PLAN.json                            thumbnail/captions.srt/description/titles/chapters
#   ./run.sh selftest                                     unit tests
#
# Between prep and render sits the editorial pass — restructure plan.json by hand
# or with an agent (docs/AGENT-WORKFLOW.md). `auto` skips it and dresses with
# deterministic graphics instead. Everything runs on your machine; nothing leaves it.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cmd="${1:?usage: run.sh auto|prep|check|render|shorts|package|selftest ...}"; shift

plan_of() {  # RAW path -> <dir>/<base>.vidpipe/plan.json
  echo "$(dirname "$1")/$(basename "${1%.*}").vidpipe/plan.json"
}

prep() {  # RAW [mode] [model]
  local raw mode model dir base work tj
  raw="$(realpath "$1")"; mode="${2:-gameplay}"; model="${3:-large-v3}"
  dir="$(dirname "$raw")"; base="$(basename "${raw%.*}")"; work="$dir/$base.vidpipe"
  mkdir -p "$work/result"
  echo "[prep 1/2] transcribing ($model, $mode)…"
  tj="$(bash "$here/transcribe.sh" "$raw" "$work" "$model" | tail -1)"
  echo "[prep 2/2] planning cuts…"
  python3 "$here/plan.py" "$raw" "$tj" -o "$work/plan.json" --mode "$mode"
  python3 - "$work/plan.json" "$work/result/final.mp4" <<'PY'
import json, sys
p = json.load(open(sys.argv[1])); p["output"] = sys.argv[2]
json.dump(p, open(sys.argv[1], "w"), indent=1)
PY
  echo "plan: $work/plan.json"
}

case "$cmd" in
prep)
  prep "$@"
  ;;
check)
  python3 "$here/check.py" "$(realpath "$1")"
  ;;
render)
  plan="$(realpath "$1")"
  python3 "$here/render.py" "$plan" "$(dirname "$plan")/work"
  ;;
shorts)
  plan="$(realpath "$1")"; shift
  python3 "$here/shorts.py" "$plan" "$(dirname "$plan")/work" "$@"
  ;;
package)
  plan="$(realpath "$1")"
  python3 "$here/package.py" "$plan" "$(dirname "$plan")/work"
  ;;
auto)
  raw="$(realpath "$1")"; mode="${2:-gameplay}"; model="${3:-large-v3}"
  prep "$raw" "$mode" "$model"
  plan="$(plan_of "$raw")"
  python3 "$here/check.py" "$plan"
  python3 "$here/autodress.py" "$plan"
  python3 "$here/check.py" "$plan"
  python3 "$here/render.py" "$plan" "$(dirname "$plan")/work"
  python3 "$here/shorts.py" "$plan" "$(dirname "$plan")/work"
  python3 "$here/package.py" "$plan" "$(dirname "$plan")/work"
  echo
  echo "done — $(dirname "$plan")/result/  (final.mp4, shorts/, thumbnail.png,"
  echo "captions.srt, description.txt, titles.txt, chapters.txt)"
  ;;
selftest)
  for m in gfx enc sfx highlights plan render shorts autodress check; do
    echo "== $m"
    python3 "$here/$m.py" --selftest
  done
  ;;
*)
  echo "unknown: $cmd" >&2; exit 1
  ;;
esac
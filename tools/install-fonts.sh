#!/usr/bin/env bash
# Fetch the two OFL font families vidpipe renders with (Fira Sans, JetBrains
# Mono) into the user font dir. No root needed. Chromium and libass both pick
# them up via fontconfig after fc-cache.
set -euo pipefail
dir="${XDG_DATA_HOME:-$HOME/.local/share}/fonts/vidpipe"
base="https://github.com/google/fonts/raw/main/ofl"
mkdir -p "$dir"
curl -sfL --retry 3 -o "$dir/FiraSans-Regular.ttf"  "$base/firasans/FiraSans-Regular.ttf"
curl -sfL --retry 3 -o "$dir/FiraSans-Bold.ttf"     "$base/firasans/FiraSans-Bold.ttf"
curl -sfL --retry 3 -o "$dir/JetBrainsMono.ttf"     "$base/jetbrainsmono/JetBrainsMono%5Bwght%5D.ttf"
curl -sfL --retry 3 -o "$dir/OFL.txt"               "$base/firasans/OFL.txt"
fc-cache -f "$dir" >/dev/null
echo "fonts installed to $dir"
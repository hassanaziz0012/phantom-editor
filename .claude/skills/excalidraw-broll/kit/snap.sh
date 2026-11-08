#!/usr/bin/env bash
# Build, then snapshot every B-roll scene 0.6 s before it ends (everything should be on screen).
#   bash snap.sh "<project>/broll" [OUT_NAME] [TIMES]   TIMES = comma list to override (e.g. seam checks)
# Prints the contact sheet paths: Read those (4-5 images), not the single frames.
set -euo pipefail
B="$(cd "$1" && pwd)"; OUT="$B/research/${2:-snap}"
cd "$B"
python3 scripts/build.py >/dev/null
TIMES="${3:-$(python3 scripts/tools.py snaptimes)}"
rm -rf "$OUT"
npx hyperframes snapshot --at "$TIMES" --no-end -o "$OUT" >/dev/null 2>&1 || { echo "snapshot failed"; exit 1; }
ls "$OUT"/contact-sheet*.jpg

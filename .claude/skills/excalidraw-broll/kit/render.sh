#!/usr/bin/env bash
# Render, verify and deliver. Run it with run_in_background.
#   bash render.sh "<project>/broll"                  full film, delivered as ../final.mp4
#   bash render.sh "<project>/broll" --test FROM TO   a short span only -> renders/test.mp4 (not delivered)
# Stages (each one timed; the table is printed at the end and appended to research/render-timings.log):
#   wait-face  setup.sh's background face.mp4 encode
#   build      build.py -> index.html (previews) + render.html (packed B-roll windows) + research/render-plan.json
#   chrome     HyperFrames renders render.html: only the B-roll windows, no face square, no audio
#   assemble   assemble.py (ffmpeg): face gaps from face.mp4 + packed B-roll + face square + voice/BGM/SFX
#   verify     duration + loudness
#   sheet      research/final-sheet.jpg (24 frames across the film) for one spot-check Read
#   deliver    ../final.mp4 (the face-only original is kept as ../final-face.mp4)
# Progress at any time: python3 <kit>/progress.py "<project>/broll"  (or cat research/progress.txt)
set -euo pipefail
B="$(cd "$1" && pwd)"; cd "$B"
KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST=""; [ "${2:-}" = "--test" ] && TEST="$3:$4"
SRC=../final.mp4; [ -f ../final-face.mp4 ] && SRC=../final-face.mp4   # the face-only video

# Kit-owned scripts (never scenes.py): projects set up before the packed render lack assemble.py.
if [ ! -f scripts/plumbing/assemble.py ]; then
  rm -rf scripts/plumbing
  cp -r "$KIT/scripts/plumbing" "$KIT"/scripts/{sketch.py,doodles.py,blocks.py,build.py,tools.py} scripts/
  echo "updated kit scripts in $B/scripts (scenes.py untouched)"
fi

TIMES=(); CUR=""; T0=0; START=$(date +%s)
stage() {
  local now; now=$(date +%s)
  [ -n "$CUR" ] && TIMES+=("$(printf '%-10s %6ds' "$CUR" $((now - T0)))")
  CUR="$1"; T0=$now
  echo "$1" > research/render-stage.txt
}
report() {
  stage done
  local total=$(( $(date +%s) - START ))
  { echo "== $(date '+%F %T')  ${TEST:+test $TEST  }film ${FILM:-?} s, Chrome rendered ${PACKED:-?} s"
    printf '%s\n' "${TIMES[@]}"
    printf '%-10s %6ds\n' total "$total"; } | tee -a research/render-timings.log
}
trap 'kill ${PROG:-0} 2>/dev/null || true' EXIT

stage wait-face
while [ ! -f assets/video/face.mp4 ]; do sleep 10; done
[ -f assets/video/pip.mp4 ] || { echo "no assets/video/pip.mp4 (the face square): re-run setup.sh"; exit 1; }

stage build
RENDER_SPAN="$TEST" python3 scripts/build.py | tail -1
read -r FILM PACKED < <(python3 -c "import json;p=json.load(open('research/render-plan.json'));print('%.1f %.1f'%(p['span'][1]-p['span'][0],p['packed_total']))")

stage chrome
rm -f renders/.progress.json
( while sleep 30; do python3 "$KIT/progress.py" "$B" > research/progress.tmp 2>/dev/null \
    && mv research/progress.tmp research/progress.txt; done ) &
PROG=$!
echo "progress: python3 $KIT/progress.py \"$B\"   (or cat \"$B/research/progress.txt\")"
npx hyperframes render -c render.html -o renders/packed.mp4 --quality delivery --crf 12 --quiet > research/render.log 2>&1 \
  || { echo "RENDER FAILED, see research/render.log"; rg -i "error|fail" research/render.log | tail -5; exit 1; }

stage assemble
OUT=renders/broll.mp4; [ -n "$TEST" ] && OUT=renders/test.mp4
python3 "$KIT/scripts/plumbing/assemble.py" renders/packed.mp4 "$OUT"
kill $PROG 2>/dev/null || true

stage verify
D=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$OUT")
echo "duration $D s (expected $FILM s; face-only video: $(ffprobe -v error -show_entries format=duration -of csv=p=0 "$SRC") s)"
ffmpeg -nostats -i "$OUT" -af ebur128=peak=true -f null - 2>&1 | rg -A14 Summary | rg "I:|Peak:"

stage sheet
SHEET=research/final-sheet.jpg; [ -n "$TEST" ] && SHEET=research/test-sheet.jpg
rm -rf research/fin && mkdir -p research/fin
python3 - "$D" "$OUT" "${TEST%%:*}" <<'EOF'
import subprocess, sys
d, out, off = float(sys.argv[1]), sys.argv[2], float(sys.argv[3] or 0)
for i in range(24):
    t = min(d - 0.1, 2 + i * (d - 4) / 23) if d > 6 else i * d / 24
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-ss', '%.2f' % t, '-i', out, '-frames:v', '1', '-vf',
                    "scale=480:-1,drawtext=text='%.1f':x=8:y=8:fontsize=22:fontcolor=red" % (t + off),
                    'research/fin/%02d.png' % i], check=True)
EOF
ffmpeg -v error -y -pattern_type glob -i 'research/fin/*.png' -vf tile=4x6 -frames:v 1 "$SHEET"

if [ -n "$TEST" ]; then
  report
  echo "test render: $B/$OUT  (not delivered)  sheet: $B/$SHEET"
  exit 0
fi

stage deliver
# The first delivery moves the face-only video aside; later ones only replace the B-roll cut.
# (Named final-face so `phantom status` does not take it for a raw recording.)
[ -f ../final-face.mp4 ] || mv ../final.mp4 ../final-face.mp4
cp "$OUT" ../final.tmp.mp4 && mv ../final.tmp.mp4 ../final.mp4
echo "render done" > research/progress.txt
report
echo "delivered: $(cd .. && pwd)/final.mp4  (face-only original: final-face.mp4)"
echo "spot-check: $B/$SHEET"

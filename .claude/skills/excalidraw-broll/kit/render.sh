#!/usr/bin/env bash
# Render, verify and deliver. ~20-25 min for a 7-min video: run it with run_in_background.
#   bash render.sh "<project>/broll"
# Writes renders/broll.mp4, copies it to ../final-broll.mp4, prints duration + loudness, and writes
# research/final-sheet.jpg (24 frames across the film) for one spot-check Read.
set -euo pipefail
B="$(cd "$1" && pwd)"; cd "$B"
while [ ! -f assets/video/face.mp4 ]; do sleep 10; done   # setup.sh encodes it in the background
python3 scripts/build.py >/dev/null
S=$(date +%s)
npx hyperframes render -o renders/broll.mp4 --quality delivery --quiet > research/render.log 2>&1 \
  || { echo "RENDER FAILED, see research/render.log"; grep -iE "error|fail" research/render.log | tail -5; exit 1; }
echo "render took $(( $(date +%s) - S )) s"
D=$(ffprobe -v error -show_entries format=duration -of csv=p=0 renders/broll.mp4)
echo "duration $D s (final.mp4: $(ffprobe -v error -show_entries format=duration -of csv=p=0 ../final.mp4) s)"
ffmpeg -nostats -i renders/broll.mp4 -af ebur128=peak=true -f null - 2>&1 | grep -A14 Summary | grep -E "I:|Peak:"
rm -rf research/fin && mkdir -p research/fin
python3 - "$D" <<'EOF'
import subprocess, sys
d = float(sys.argv[1])
for i in range(24):
    t = 2 + i * (d - 4) / 23
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-ss', '%.2f' % t, '-i', 'renders/broll.mp4', '-frames:v', '1', '-vf',
                    "scale=480:-1,drawtext=text='%.1f':x=8:y=8:fontsize=22:fontcolor=red" % t,
                    'research/fin/%02d.png' % i], check=True)
EOF
ffmpeg -v error -y -pattern_type glob -i 'research/fin/*.png' -vf tile=4x6 -frames:v 1 research/final-sheet.jpg
cp renders/broll.mp4 ../final-broll.mp4
echo "delivered: $(cd .. && pwd)/final-broll.mp4"
echo "spot-check: $B/research/final-sheet.jpg"

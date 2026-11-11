#!/usr/bin/env bash
# Prepare <project>/broll for an Excalidraw B-roll build. Safe to re-run (never overwrites scenes.py).
#   bash setup.sh "<project dir>" ["BGM file name in Asset Library/BGM, default: Hello World.mp3"]
# Needs in <project>: final.mp4, trimmed-1word.srt (trimmed.srt is for you to read).
# After a delivered build, final.mp4 is the B-roll cut and the face-only original is final-face.mp4
# (render.sh does that), so the face source is final-face.mp4 when it exists.
set -euo pipefail
KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
P="$(cd "$1" && pwd)"
BGM_SRC="$HOME/Videos/Asset Library/BGM/${2:-Hello World.mp3}"
B="$P/broll"
SRC="$P/final.mp4"; [ -f "$P/final-face.mp4" ] && SRC="$P/final-face.mp4"
for f in final.mp4 trimmed-1word.srt; do [ -f "$P/$f" ] || { echo "missing $P/$f"; exit 1; }; done

if [ ! -f "$B/index.html" ]; then (cd "$P" && npx --yes hyperframes init broll --example blank >/dev/null); fi
mkdir -p "$B"/{scripts,research,renders,assets/{audio,sfx,img,fonts,video,logos}}

# kit code (always refreshed) + this video's scenes.py (only if missing)
rm -rf "$B/scripts/plumbing"
cp -r "$KIT/scripts/plumbing" "$KIT"/scripts/{sketch.py,doodles.py,blocks.py,build.py,tools.py} "$B/scripts/"
[ -f "$B/scripts/scenes.py" ] || cp "$KIT/scripts/scenes.py" "$B/scripts/scenes.py"
[ -d "$KIT/node/node_modules" ] || (cd "$KIT/node" && npm i --no-audit --no-fund >/dev/null)
ln -sfn "$KIT/node" "$B/scripts/node"
cp "$KIT"/assets/fonts/*.woff2 "$B/assets/fonts/"
cp -n "$KIT"/assets/sfx/* "$B/assets/sfx/" 2>/dev/null || true

DUR=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$SRC")

# face footage, seekable: HyperFrames needs keyframes <= ~1 s apart, or face clips show frozen frames.
# The pipeline now encodes final.mp4 that way, so its video stream is copied as is (seconds, no quality
# loss). An older final.mp4 with keyframes seconds apart is re-encoded in the background (~4 min).
if [ ! -f "$B/assets/video/face.mp4" ] && ! pgrep -f "face.tmp.mp4" >/dev/null; then
  MAXGAP=$(ffprobe -v error -select_streams v:0 -show_entries packet=pts_time,flags -of csv=p=0 "$SRC" \
    | awk -F, '$2 ~ /K/ { if (seen && $1 - last > max) max = $1 - last; last = $1; seen = 1 }
               { end = $1 } END { if (end - last > max) max = end - last; printf "%.3f", max }')
  if awk "BEGIN { exit !($MAXGAP <= 1.05) }"; then
    ffmpeg -y -v error -i "$SRC" -an -c:v copy -movflags +faststart "$B/assets/video/face.tmp.mp4"
    mv "$B/assets/video/face.tmp.mp4" "$B/assets/video/face.mp4"
    echo "face.mp4: copied from $(basename "$SRC") without re-encoding (keyframes ${MAXGAP} s apart)"
  else
    echo "face.mp4: $(basename "$SRC") keyframes are up to ${MAXGAP} s apart, re-encoding in the background"
    nohup sh -c "ffmpeg -y -v error -i '$SRC' -an -c:v libx264 -preset medium -crf 15 -g 30 -keyint_min 30 \
      -pix_fmt yuv420p -movflags +faststart '$B/assets/video/face.tmp.mp4' && mv '$B/assets/video/face.tmp.mp4' \
      '$B/assets/video/face.mp4'" > "$B/research/face-encode.log" 2>&1 &
  fi
fi

# the face square over the B-roll (build.py, PIP in sketch.py): a 360x360 centre crop of the face, 1 s keyframes.
# Its own small file, because the renderer extracts video frames at the source size: square clips cut from the
# 1080p face.mp4 put the extracted frames at ~2x HyperFrames' 2 GB plan cap (PLAN_TOO_LARGE).
if [ ! -f "$B/assets/video/pip.mp4" ]; then
  ffmpeg -y -v error -i "$SRC" -an -vf "crop=ih:ih:(iw-ih)/2:0,scale=360:360" -c:v libx264 -preset veryfast -crf 18 \
    -g 30 -keyint_min 30 -pix_fmt yuv420p -movflags +faststart "$B/assets/video/pip.tmp.mp4"
  mv "$B/assets/video/pip.tmp.mp4" "$B/assets/video/pip.mp4"
fi

[ -f "$B/assets/audio/voice.m4a" ] || ffmpeg -y -v error -i "$SRC" -vn -c:a aac -b:a 192k "$B/assets/audio/voice.m4a"
python3 "$B/scripts/plumbing/srt_to_words.py" "$P/trimmed-1word.srt" "$B/research/words.txt" >/dev/null

# BGM looped with crossfades to the video length, faded out, -16 LUFS (played at volume 0.08 in build.py)
if [ ! -f "$B/assets/audio/bgm.m4a" ]; then
  python3 - "$BGM_SRC" "$DUR" "$B/assets/audio/bgm.m4a" <<'EOF'
import math, subprocess, sys
src, dur, out = sys.argv[1], float(sys.argv[2]), sys.argv[3]
L = float(subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', src],
                         capture_output=True, text=True).stdout)
n = max(1, math.ceil((dur + 2) / (L - 4)))
ins = sum([['-i', src] for _ in range(n)], [])
chain, last = [], '[0]'
for i in range(1, n):
    chain.append('%s[%d]acrossfade=d=4[x%d]' % (last, i, i)); last = '[x%d]' % i
chain.append('%satrim=0:%.2f,afade=t=out:st=%.2f:d=6,loudnorm=I=-16:TP=-1.5[o]' % (last, dur + 1, dur - 5))
subprocess.run(['ffmpeg', '-y', '-v', 'error'] + ins + ['-filter_complex', ';'.join(chain), '-map', '[o]',
                '-c:a', 'aac', '-b:a', '160k', out], check=True)
EOF
fi

[ -f "$B/research/sfx-peaks.json" ] || (cd "$B" && python3 scripts/plumbing/sfx_peaks.py assets/sfx research/sfx-peaks.json >/dev/null)

if [ ! -f "$B/HANDOFF.md" ]; then cat > "$B/HANDOFF.md" <<EOF
# Excalidraw B-roll build: $(basename "$P")
Skill: phantom-editor/.claude/skills/excalidraw-broll. Output: ../final.mp4 (face-only original kept as ../final-face.mp4)
## Status
- [done] setup.sh
- [ ] storyboard (scripts/scenes.py board())
- [ ] scenes written, build + tools.py share
- [ ] snap.sh review + fixes
- [ ] render.sh -> ../final.mp4 verified
## Notes
EOF
fi

echo "ready: $B  (video $(printf %.1f "$DUR") s, $(wc -l < "$B/research/words.txt") words)"
echo "face.mp4 encoding in background: $( [ -f "$B/assets/video/face.mp4" ] && echo done || echo running )"

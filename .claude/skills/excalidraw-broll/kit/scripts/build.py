#!/usr/bin/env python3
"""Regenerate compositions/ + index.html from scenes.py. Run from the broll/ folder:
    python3 scripts/build.py            # everything
    python3 scripts/build.py s07 s12    # rewrite only these scene files (index is always rewritten)
Never hand-edit compositions/ or index.html."""
import math, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from plumbing import core  # noqa: E402
import sketch  # noqa: E402

ROOT = os.path.abspath(os.path.join(HERE, '..'))
FPS = 30


def probe(p):
    return float(subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', p],
                                capture_output=True, text=True, check=True).stdout.strip())


# One frame shorter than the shorter of voice / face so the film never ends on a black frame.
TOTAL = (math.floor(min(probe(os.path.join(ROOT, 'assets/audio/voice.m4a')),
                        probe(os.path.join(ROOT, '../final.mp4'))) * FPS) - 1) / FPS

core.setup(
    root=ROOT, total=round(TOTAL, 3), fps=FPS,
    transcript='research/words.txt',
    voice='assets/audio/voice.m4a',
    bgm='assets/audio/bgm.m4a', bgm_volume=0.08, bgm_fade_in=2.0,
    peaks='research/sfx-peaks.json',
    bg='#000',
    css=sketch.FONT_CSS,
    js=sketch.SK_JS,
)

import scenes  # noqa: E402

# Face footage only where it can be seen: face segments + PAD around each one for the paper slide in/out.
# (One 7-min <video> makes the renderer extract every frame and fail with PLAN_TOO_LARGE, 2 GB cap.)
PAD = 0.7
board = scenes.board()
merged = []
for i, (t, fn, _) in enumerate(board):
    if fn is not None:
        continue
    t1 = board[i + 1][0] if i + 1 < len(board) else TOTAL
    w = [max(0.0, t - PAD), min(TOTAL, t1 + PAD)]
    if merged and w[0] <= merged[-1][1]:
        merged[-1][1] = max(merged[-1][1], w[1])
    else:
        merged.append(w)
vids = []
for j, (a, z) in enumerate(merged):
    a, z = round(a * FPS) / FPS, round(z * FPS) / FPS
    vids.append('<video id="face%02d" class="clip" src="assets/video/face.mp4" muted playsinline data-start="%.3f" '
                'data-duration="%.3f" data-media-start="%.3f" data-track-index="0" style="position:absolute;left:0;'
                'top:0;width:1920px;height:1080px;object-fit:cover;z-index:0"></video>' % (j, a, z - a, a))
core.CFG['underlay'] = '\n'.join(vids)

scenes.register()
core.build(only=set(sys.argv[1:]) or None)
print('face clips %d (%.1f s of footage extracted)' % (len(vids), sum(z - a for a, z in merged)))

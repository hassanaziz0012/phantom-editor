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
from sketch import PIP  # noqa: E402

ROOT = os.path.abspath(os.path.join(HERE, '..'))
FPS = 30


def probe(p):
    return float(subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', p],
                                capture_output=True, text=True, check=True).stdout.strip())


# The face-only video: final-face.mp4 once render.sh has delivered a B-roll cut as final.mp4.
FACE_SRC = os.path.join(ROOT, '../final-face.mp4')
if not os.path.exists(FACE_SRC):
    FACE_SRC = os.path.join(ROOT, '../final.mp4')

# One frame shorter than the shorter of voice / face so the film never ends on a black frame.
TOTAL = (math.floor(min(probe(os.path.join(ROOT, 'assets/audio/voice.m4a')), probe(FACE_SRC)) * FPS) - 1) / FPS

core.setup(
    root=ROOT, total=round(TOTAL, 3), fps=FPS,
    transcript='research/words.txt',
    voice='assets/audio/voice.m4a',
    bgm='assets/audio/bgm.m4a', bgm_volume=0.08, bgm_fade_in=2.0,
    peaks='research/sfx-peaks.json',
    bg='#000',
    css=sketch.FONT_CSS,
    js=sketch.SK_JS,
    # the face square over the B-roll (pip.mp4, already cropped square): rounded, scales from its corner
    head='<style>.pip{position:absolute;left:%dpx;top:%dpx;width:%dpx;height:%dpx;object-fit:cover;'
         'border-radius:40px;border:4px solid #1e1e1e;box-shadow:0 12px 40px rgba(0,0,0,.3);transform-origin:100%% 100%%;'
         'z-index:3}</style>' % PIP,
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

# Face picture-in-picture: one clip per run of back-to-back B-roll scenes that show it (face=False in a board
# entry turns it off for that scene). It pops in once the paper has slid in and shrinks away before the
# paper leaves, and stays put while consecutive scenes pan. It plays pip.mp4 (setup.sh), a 360x360 crop of
# the face: the renderer extracts frames at source size, and 1080p frames under every B-roll scene would
# blow the 2 GB plan cap.
runs = []
for i, (t, fn, kw) in enumerate(board):
    t1 = board[i + 1][0] if i + 1 < len(board) else TOTAL
    if fn is None or kw.get('face', True) is False:
        continue
    if runs and abs(runs[-1][1] - t) < 1e-6:
        runs[-1][1] = t1
    else:
        runs.append([t, t1])
pips, pjs = [], []
for a, z in runs:
    a, z = round(a * FPS) / FPS, round(z * FPS) / FPS
    if z - a < 1.5:   # too short to pop in and out
        continue
    pid = 'pip%02d' % len(pips)
    pips.append('<video id="%s" class="clip pip" src="assets/video/pip.mp4" muted playsinline data-start="%.3f" '
                'data-duration="%.3f" data-media-start="%.3f" data-track-index="3"></video>' % (pid, a, z - a, a))
    pjs.append("tl.fromTo('#%s',{opacity:0,scale:0.35},{opacity:1,scale:1,duration:0.5,ease:'back.out(1.6)'},%.3f);"
               "tl.to('#%s',{opacity:0,scale:0.4,duration:0.28,ease:'power3.in'},%.3f);" % (pid, a + 0.3, pid, z - 0.55))
core.CFG['overlay'] = '\n'.join(pips)
core.CFG['main_js'] = '\n'.join(pjs)

scenes.register()
core.build(only=set(sys.argv[1:]) or None)

# ---- render plan (render.sh). Chrome renders only the B-roll windows, packed back to back into render.html
# (each window = a run of B-roll scenes + PAD of face on both sides for the paper slide, no face square, no
# audio). assemble.py then rebuilds the film with ffmpeg: face gaps straight from face.mp4, the face square
# overlaid from pip.mp4, and voice + BGM + SFX mixed. RENDER_SPAN="from:to" (seconds) keeps only the windows
# that touch that span, for short test renders.
span = [float(x) for x in os.environ['RENDER_SPAN'].split(':')] if os.environ.get('RENDER_SPAN') else [0.0, TOTAL]
fr = lambda x: round(x * FPS) / FPS
wins = []
for i, (t, fn, _) in enumerate(board):
    if fn is None:
        continue
    t1 = board[i + 1][0] if i + 1 < len(board) else TOTAL
    w = [fr(max(0.0, t - PAD)), fr(min(TOTAL, t1 + PAD))]
    if wins and w[0] <= wins[-1][1] + 1e-6:
        wins[-1][1] = max(wins[-1][1], w[1])
    else:
        wins.append(w)
wins = [w for w in wins if w[1] > span[0] and w[0] < span[1]]
packed, o, under = [], 0.0, []
for a, z in wins:
    packed.append([a, z, round(o, 3)])
    for fa, fz in merged:   # face footage visible inside this window (the paper slides over it)
        x0, x1 = max(a, fa), min(z, fz)
        if x1 - x0 > 1e-6:
            under.append('<video id="face%02d" class="clip" src="assets/video/face.mp4" muted playsinline '
                         'data-start="%.3f" data-duration="%.3f" data-media-start="%.3f" data-track-index="0" '
                         'style="position:absolute;left:0;top:0;width:1920px;height:1080px;object-fit:cover;z-index:0">'
                         '</video>' % (len(under), o + x0 - a, x1 - x0, x0))
    o = fr(o + z - a)
core.write_packed('render.html', packed, '\n'.join(under), max(o, 1.0 / FPS))
import json  # noqa: E402
json.dump(dict(fps=FPS, total=round(TOTAL, 3), span=span, packed_total=round(o, 3), windows=packed,
               pip_runs=[[round(fr(a), 3), round(fr(z), 3)] for a, z in runs if fr(z) - fr(a) >= 1.5],
               pip_box=list(PIP), audio=core.audio_events()),
          open(os.path.join(ROOT, 'research/render-plan.json'), 'w'), indent=1)
print('face clips %d (%.1f s of footage extracted), face squares over B-roll %d' % (len(vids), sum(z - a for a, z in merged), len(pips)))
print('render windows %d: Chrome renders %.1f s of %.1f s (%d face clips inside)' % (len(packed), o, span[1] - span[0], len(under)))

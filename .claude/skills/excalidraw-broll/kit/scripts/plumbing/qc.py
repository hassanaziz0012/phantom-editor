#!/usr/bin/env python3
"""Quality check of a rendered B-roll cut (render.sh stage 'qc'). Run from the broll/ folder:
    python3 <kit>/scripts/plumbing/qc.py renders/broll.mp4      # or ../final.mp4, renders/test.mp4
Reads research/render-plan.json (windows, face-square runs, span). Checks, on the finished video:
  * black frames: any frame whose mean luma is near black (one frame is enough to fail);
  * freezes: the picture frozen >= 0.5 s outside the B-roll runs (where only the face is on screen);
  * cut alignment: at every B-roll window edge the face is matched frame by frame against face.mp4;
    the face inside a window running a frame early/late is a stutter at the cut.
Writes research/qc.txt; exit 1 when anything fails (render.sh then does not deliver).
QC_ALLOW_DARK=1 skips the black-frame check for a film that shows dark frames on purpose.
"""
import json
import os
import re
import subprocess
import sys

PLAN, FACE = 'research/render-plan.json', 'assets/video/face.mp4'
DARK = 24          # mean luma (16 = video black); the whiteboard and the face sit far above
W, H = 64, 48      # match size; the crop is the middle-left of the frame (the face, away from the
CROP = 'crop=800:600:520:120'   # face square and the side the paper slides in from)


def scan(video):
    """One decode: per-frame mean luma + freezedetect. Returns (fps, [(t, yavg)], [(start, end)])."""
    r = subprocess.run(['ffmpeg', '-v', 'info', '-nostats', '-i', video, '-an', '-vf',
                        'scale=192:108,freezedetect=n=0.003:d=0.5,signalstats,'
                        'metadata=print:key=lavfi.signalstats.YAVG:file=-', '-f', 'null', '-'],
                       capture_output=True, text=True)
    lum, t = [], None
    for line in r.stdout.splitlines():
        m = re.search(r'pts_time:([\d.]+)', line)
        if m:
            t = float(m.group(1))
        m = re.search(r'YAVG=([\d.]+)', line)
        if m and t is not None:
            lum.append((t, float(m.group(1))))
    starts = [float(x) for x in re.findall(r'freeze_start: ([\d.]+)', r.stderr)]
    ends = [float(x) for x in re.findall(r'freeze_end: ([\d.]+)', r.stderr)]
    if len(ends) < len(starts):
        ends.append(lum[-1][0] if lum else 0.0)
    return lum, list(zip(starts, ends))


def frames(video, k0, n, fps):
    """n frames from frame index k0 (seek a quarter frame early: an accurate seek keeps frames >= the time)."""
    r = subprocess.run(['ffmpeg', '-v', 'error', '-ss', '%.4f' % max(0.0, (k0 - 0.25) / fps), '-i', video,
                        '-frames:v', str(n), '-vf', '%s,scale=%d:%d,format=gray' % (CROP, W, H),
                        '-f', 'rawvideo', '-'], capture_output=True).stdout
    return [r[i:i + W * H] for i in range(0, len(r), W * H)]


def diff(a, b):
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


def edge_shift(video, film_k, out_k, fps, n=12):
    """Shift (frames) between the output's face and face.mp4 over n frames: 0 = aligned, None = no clear match."""
    out, ref = frames(video, out_k, n, fps), frames(FACE, film_k - 2, n + 4, fps)
    votes = {}
    for i in range(min(len(out), len(ref) - 4)):
        e = [diff(out[i], ref[i + 2 + s]) for s in range(-2, 3)]
        if sorted(e)[1] - min(e) > 0.3:          # only frames where one shift clearly wins (the face moved)
            s = e.index(min(e)) - 2
            votes[s] = votes.get(s, 0) + 1
    if sum(votes.values()) < 3:
        return None
    return max(votes, key=votes.get)


def main(video):
    plan = json.load(open(PLAN))
    fps, (s0, s1) = plan['fps'], plan['span']
    problems = []
    lum, freezes = scan(video)
    if not lum:
        sys.exit('qc: could not read %s' % video)
    if os.environ.get('QC_ALLOW_DARK') != '1':
        dark = [t for t, y in lum if y < DARK]
        if dark:
            problems.append('black frames (%d) at film %s' % (len(dark), ', '.join('%.3f' % (t + s0) for t in dark[:12])))
    runs = plan['pip_runs']
    for a, z in freezes:
        fa, fz = a + s0, z + s0
        if not any(ra - 0.4 <= fa and fz <= rz + 0.4 for ra, rz in runs):
            problems.append('frozen picture outside B-roll: film %.2f-%.2f' % (fa, fz))
    bad_edges, checked = [], 0
    for a, z, _ in plan['windows']:
        # face from the packed render: from the window start until the paper covers it, and the last frames
        # before the window end. (a, z are frame-aligned film times.)
        for label, k in (('start', round(a * fps) + 1), ('end', round(z * fps) - 13)):
            if not (s0 * fps <= k and k + 12 <= s1 * fps):
                continue
            sh = edge_shift(video, k, k - round(s0 * fps), fps)
            if sh is None:
                continue
            checked += 1
            if sh != 0:
                bad_edges.append('%s %.2f: face %+d frame' % (label, k / fps, sh))
    if bad_edges:
        problems.append('face off by a frame at B-roll cuts (stutter): ' + '; '.join(bad_edges))
    lines = ['qc %s: %d frames, %d freezes, %d cut edges checked' % (video, len(lum), len(freezes), checked)]
    lines += ['FAIL ' + p for p in problems] or ['PASS']
    os.makedirs('research', exist_ok=True)
    with open('research/qc.txt', 'w') as f:
        f.write('\n'.join(lines) + '\n')
    print('\n'.join(lines))
    return 1 if problems else 0


if __name__ == '__main__':
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1]))

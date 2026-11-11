#!/usr/bin/env python3
"""Render progress for render.sh, deterministic and cheap: one line, no tokens spent reading logs.
    python3 progress.py "<project>/broll"
Follows render.sh's stage (research/render-stage.txt). During 'chrome' it counts the frames HyperFrames has
already encoded into renders/work-*/video-only.mp4 (H.264 slices with first_mb == 0 in the mdat), compares them
with the packed length from research/render-plan.json, and estimates the time left from the encode's start
(the file's birth time). During 'assemble' it reads ffmpeg's -progress file. Reads only the bytes added since the last call (state in
renders/.progress.json), so it is safe to run every few seconds. render.sh also writes its output to
research/progress.txt every 30 s.
"""
import glob
import json
import os
import struct
import subprocess
import sys
import time

FPS = 30
TAIL_MIN = 2.0   # audio mux, loudness pass, contact sheet, delivery (minutes)


def duration(path):
    out = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', path],
                         capture_output=True, text=True).stdout.strip()
    return float(out) if out else None


def birth(path):
    out = subprocess.run(['stat', '-c', '%W', path], capture_output=True, text=True).stdout.strip()
    b = int(out) if out.isdigit() else 0
    return b or os.stat(path).st_mtime


def count(path, state):
    """Frames in an unfinished mp4 (no moov yet). Resumes from the saved offset."""
    st = state if state.get('path') == path else {'path': path, 'off': None, 'n': 0}
    with open(path, 'rb') as f:
        if st['off'] is None:   # locate mdat payload
            i = 0
            while True:
                f.seek(i)
                h = f.read(16)
                if len(h) < 8:
                    return st
                sz, typ = struct.unpack('>I4s', h[:8])
                if typ == b'mdat':
                    st['off'] = i + (16 if sz == 1 else 8)
                    break
                if sz < 8:
                    return st
                i += sz
        f.seek(st['off'])
        data = f.read()
    p = 0
    while p + 5 <= len(data):
        ln = struct.unpack('>I', data[p:p + 4])[0]
        if ln == 0 or p + 4 + ln > len(data):
            break
        if data[p + 4] & 0x1f in (1, 5) and data[p + 5] & 0x80:
            st['n'] += 1
        p += 4 + ln
    st['off'] += p
    return st


def plan_info(broll):
    try:
        p = json.load(open(os.path.join(broll, 'research', 'render-plan.json')))
        return p['packed_total'], p['span'][1] - p['span'][0]
    except (OSError, ValueError, KeyError):
        return None, None


def assemble_line(broll, film):
    """ffmpeg -progress file of the assemble stage."""
    try:
        kv = dict(l.strip().split('=', 1) for l in open(os.path.join(broll, 'research', 'assemble-progress.txt'))
                  if '=' in l)
    except OSError:
        return 'assemble: starting ffmpeg'
    done = int(kv.get('out_time_us', '0') or 0) / 1e6
    speed = kv.get('speed', '?').strip()
    if not film:
        return 'assemble: %.0f s written (%s)' % (done, speed)
    pct = min(100.0, 100 * done / film)
    try:
        sp = float(speed.rstrip('x'))
        eta = ' ~%.0f min left' % ((film - done) / sp / 60) if sp > 0 else ''
    except ValueError:
        eta = ''
    return 'assemble (ffmpeg) %.0f%%  %.0f / %.0f s  %s%s' % (pct, done, film, speed, eta)


def main(broll):
    try:
        stage = open(os.path.join(broll, 'research', 'render-stage.txt')).read().strip()
    except OSError:
        stage = ''
    packed_total, film = plan_info(broll)
    if stage == 'assemble':
        print(assemble_line(broll, film))
        return
    if stage in ('verify', 'sheet', 'deliver'):
        print('render finishing: %s (under a minute)' % stage)
        return
    if stage in ('wait-face', 'build'):
        print('render stage: %s' % stage)
        return
    if stage == 'done':
        print('render done')
        return
    sf = os.path.join(broll, 'renders', '.progress.json')
    total = packed_total or duration(os.path.join(broll, 'assets', 'video', 'face.mp4'))
    works = sorted(glob.glob(os.path.join(broll, 'renders', 'work-*')), key=os.path.getmtime)
    vids = [os.path.join(w, 'video-only.mp4') for w in works if os.path.exists(os.path.join(w, 'video-only.mp4'))]
    if not works:
        print('render not started')
        return
    if not vids:
        m = (time.time() - os.path.getmtime(works[-1])) / 60
        print('chrome preparing: extracting video frames / loading page (%.0f min in)' % m)
        return
    vid = vids[-1]
    try:
        state = json.load(open(sf))
    except (OSError, ValueError):
        state = {}
    st = count(vid, state)
    try:
        json.dump(st, open(sf, 'w'))
    except OSError:
        pass
    done_s = st['n'] / FPS
    mins = max((time.time() - birth(vid)) / 60, 0.01)
    rate = done_s / mins   # seconds of video per minute
    if not total:
        print('chrome: %.1f s done, %.1f s/min' % (done_s, rate))
        return
    pct = min(100.0, 100 * done_s / total)
    if pct >= 99.9:
        print('chrome done (%.0f s); next: ffmpeg assemble' % total)
        return
    eta = (total - done_s) / rate if rate > 0 else float('nan')
    tail = film / 60 if film else TAIL_MIN   # assemble runs at very roughly 1x realtime
    print('chrome %.0f%%  %.1f / %.1f s rendered  %.1f s/min  ~%.0f min left (+~%.0f min assemble/finish)'
          % (pct, done_s, total, rate, eta, tail))


if __name__ == '__main__':
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(os.path.abspath(sys.argv[1]))

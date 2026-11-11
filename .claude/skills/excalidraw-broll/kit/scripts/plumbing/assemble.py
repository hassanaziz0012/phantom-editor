#!/usr/bin/env python3
"""Rebuild the film from the packed B-roll render (render.sh step 'assemble'). Run from the broll/ folder:
    python3 scripts/plumbing/assemble.py renders/packed.mp4 renders/broll.mp4
Reads research/render-plan.json (written by build.py). One ffmpeg pass:
  * base = assets/video/face.mp4 (the face-only film); every packed B-roll window is laid back over it at its
    original time (setpts remap + overlay enabled only inside the windows), so face gaps cost no browser time;
  * the face square = assets/video/pip.mp4 with a rounded mask, dark border and soft shadow (PNG decor made
    once with ffmpeg), popping in/out per B-roll run like the old in-browser version (back.out 1.6 / power3.in);
  * the soundtrack = voice + BGM (fade-in) + every SFX at its slot, mixed here instead of in the browser,
    with a peak limiter.
ffmpeg writes progress to research/assemble-progress.txt (progress.py reads it).
"""
import json
import os
import subprocess
import sys
import time

PLAN = 'research/render-plan.json'
FACE, PIP = 'assets/video/face.mp4', 'assets/video/pip.mp4'
DECO, MASK = 'assets/video/pip-deco.png', 'assets/video/pip-mask.png'
M = 50          # decor margin around the 360 px square (room for the shadow)
CAN = 480       # decor canvas size
BORDER, RAD = 4, 40
X264 = ['-c:v', 'libx264', '-preset', 'veryfast', '-crf', '15', '-pix_fmt', 'yuv420p', '-g', '30',
        '-x264-params', 'aq-mode=3:aq-strength=0.8:deblock=1,1', '-colorspace', 'bt709', '-color_primaries', 'bt709',
        '-color_trc', 'bt709']


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        sys.exit('ffmpeg failed:\n' + r.stderr[-2500:])


def rrect(cx, cy, half, rad):
    """geq expression: 0..1 coverage of a rounded square (anti-aliased edge)."""
    dx = 'max(abs(X-%s)-%s,0)' % (cx, half - rad)
    dy = 'max(abs(Y-%s)-%s,0)' % (cy, half - rad)
    return 'clip(%s+0.5-hypot(%s,%s),0,1)' % (rad, dx, dy)


def decor():
    """pip-deco.png: shadow + dark rounded border square (480x480 RGBA, square at M,M).
    pip-mask.png: rounded mask for the 352 px video inside the border."""
    if os.path.exists(DECO) and os.path.exists(MASK):
        return
    inner = 360 - 2 * BORDER
    run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=black:s=%dx%d,format=gray' % (inner, inner),
         '-vf', "geq=lum='255*%s'" % rrect((inner - 1) / 2, (inner - 1) / 2, inner / 2, RAD - BORDER),
         '-frames:v', '1', MASK])
    c = M + 180 - 0.5
    shadow = "geq=r=0:g=0:b=0:a='77*%s'" % rrect(c, c + 12, 180, RAD)          # rgba(0,0,0,.3), 12 px down
    border = "geq=r=30:g=30:b=30:a='255*%s'" % rrect(c, c, 180, RAD)            # #1e1e1e
    run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=black@0:s=%dx%d,format=rgba' % (CAN, CAN),
         '-filter_complex', "[0]split[a][b];[a]%s,gblur=sigma=20:planes=8[s];[b]%s[o];[s][o]overlay=format=auto"
         % (shadow, border), '-frames:v', '1', DECO])


def scale_expr(a, z):
    """Scale of the decor at time t for a run a..z: pop in at a+0.3 (0.5 s back.out 1.6, from 0.35),
    shrink from z-0.55 (0.28 s power3.in, to 0.4)."""
    ti, to = a + 0.3, z - 0.55
    p = 'min(max((t-%.3f)/0.5,0),1)' % ti
    back = '(1+2.6*pow(%s-1,3)+1.6*pow(%s-1,2))' % (p, p)
    q = 'min(max((t-%.3f)/0.28,0),1)' % to
    return "if(lt(t,%.3f),0.35+0.65*%s,1-0.6*pow(%s,3))" % (to, back, q)


def audio_graph(ev, first_input):
    """Inputs and filter lines for the soundtrack; returns (input args, filter lines, out label)."""
    args, lines, labels = ['-i', ev['voice'][0]], [], []
    k = first_input
    lines.append('[%d:a]volume=%.3f[av]' % (k, ev['voice'][1]))
    labels.append('[av]')
    k += 1
    if ev['bgm']:
        f, vol, fade = ev['bgm']
        args += ['-i', f]
        lines.append('[%d:a]volume=%.3f,afade=t=in:st=0:d=%.2f[ab]' % (k, vol, fade))
        labels.append('[ab]')
        k += 1
    files = sorted({s[0] for s in ev['sfx']})
    for f in files:
        uses = [s for s in ev['sfx'] if s[0] == f]
        args += ['-i', f]
        outs = ''.join('[x%d_%d]' % (k, j) for j in range(len(uses)))
        lines.append('[%d:a]asplit=%d%s' % (k, len(uses), outs) if len(uses) > 1 else '[%d:a]anull%s' % (k, outs))
        for j, (_, start, slot, vol) in enumerate(uses):
            lab = '[s%d_%d]' % (k, j)
            lines.append('[x%d_%d]atrim=0:%.3f,volume=%.3f,adelay=%d:all=1%s' % (k, j, slot, vol, round(start * 1000), lab))
            labels.append(lab)
        k += 1
    lines.append('%samix=inputs=%d:normalize=0:dropout_transition=0,atrim=0:%.3f,'
                 'alimiter=limit=0.9:level=disabled:attack=5:release=50,aresample=48000,'
                 'aformat=channel_layouts=mono,pan=stereo|c0=c0|c1=c0[aout]'   # mono voice on both sides, as HyperFrames did
                 % (''.join(labels), len(labels), ev['total']))
    return args, lines, '[aout]'


def main(packed, out):
    t_start = time.time()
    plan = json.load(open(PLAN))
    fps, (s0, s1) = plan['fps'], plan['span']
    decor()
    # ---- video
    wins = plan['windows']
    remap = '0'
    for a, z, o in wins:   # packed frame N -> film time (latest window tested first: it wraps the earlier ones)
        remap = 'if(gte(N,%d),(N-%d)/%d+%.6f,%s)' % (round(o * fps), round(o * fps), fps, a, remap)
    enable = '+'.join('between(t,%.4f,%.4f)' % (a - 0.5 / fps, z - 0.5 / fps) for a, z, _ in wins) or '0'
    px, py, pw, ph = plan['pip_box']
    ax, ay = px + pw, py + ph                  # the square's bottom-right corner stays put while it scales
    v = ['[0:v]setpts=PTS-STARTPTS[face]',
         "[1:v]setpts='(%s)/TB'[pk]" % remap,
         "[face][pk]overlay=eof_action=pass:repeatlast=0:enable='%s'[base0]" % enable,
         '[2:v]scale=%d:%d,format=rgba[pv]' % (360 - 2 * BORDER, 360 - 2 * BORDER),
         '[pv][4:v]alphamerge[pm]',
         '[3:v][pm]overlay=%d:%d:shortest=1:format=auto[pd]' % (M + BORDER, M + BORDER)]
    runs = [r for r in plan['pip_runs'] if r[1] > s0 and r[0] < s1]
    if runs:
        v.append('[pd]split=%d%s' % (len(runs), ''.join('[q%d]' % i for i in range(len(runs)))) if len(runs) > 1
                 else '[pd]null[q0]')
    cur = 'base0'
    for i, (a, z) in enumerate(runs):
        sx = scale_expr(a, z)
        v.append("[q%d]trim=start=%.3f:end=%.3f,fade=t=in:st=%.3f:d=0.19:alpha=1,fade=t=out:st=%.3f:d=0.28:alpha=1,"
                 "scale=w='2*trunc(%d*(%s)/2)':h='2*trunc(%d*(%s)/2)':eval=frame[p%d]"
                 % (i, a, z, a + 0.3, z - 0.55, CAN, sx, CAN, sx, i))
        v.append("[%s][p%d]overlay=x='%d-%d*overlay_w/%d':y='%d-%d*overlay_h/%d':eof_action=pass:format=auto[b%d]"
                 % (cur, i, ax, M + pw, CAN, ay, M + ph, CAN, i))
        cur = 'b%d' % i
    v.append('[%s]trim=start=%.3f:end=%.3f,setpts=PTS-STARTPTS[vout]' % (cur, s0, s1))
    # ---- audio (inputs from 5 on)
    a_args, a_lines, a_out = audio_graph(plan['audio'], 5)
    a_lines.append('%satrim=start=%.3f:end=%.3f,asetpts=PTS-STARTPTS[afin]' % (a_out, s0, s1))
    graph = ';\n'.join(v + a_lines)
    os.makedirs('renders', exist_ok=True)
    with open('renders/assemble-graph.txt', 'w') as f:
        f.write(graph)
    cmd = ['ffmpeg', '-v', 'error', '-y', '-nostdin', '-progress', 'research/assemble-progress.txt',
           '-i', FACE, '-i', packed, '-i', PIP,
           '-loop', '1', '-framerate', str(fps), '-i', DECO, '-loop', '1', '-framerate', str(fps), '-i', MASK] + a_args + [
           '-/filter_complex', 'renders/assemble-graph.txt', '-map', '[vout]', '-map', '[afin]', '-r', str(fps)] + X264 + [
           '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', '-t', '%.3f' % (s1 - s0), out]
    run(cmd)
    print('assembled %s (%.1f s of film) in %.0f s' % (out, s1 - s0, time.time() - t_start))


if __name__ == '__main__':
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])

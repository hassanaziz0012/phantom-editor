#!/usr/bin/env python3
"""Cheap helpers. Run from the broll/ folder.
  python3 scripts/tools.py words [FROM TO]   compact transcript "t:word" (14 per line) for planning anchors
  python3 scripts/tools.py plan              the whole transcript as numbered sentences, one line each with its
                                             start time: read this once instead of trimmed.srt for storyboarding
  python3 scripts/tools.py find "phrase" ["phrase2" ...]
                                             start times of each phrase. One phrase: every match with context.
                                             Several: one compact line per phrase "phrase: t1 t2 ..." (use it to
                                             collect all of a storyboard's anchors in one call)
  python3 scripts/tools.py share             face vs B-roll seconds and % (needs a build: reads scene-list.txt)
  python3 scripts/tools.py snaptimes [BACK]  comma list: each B-roll scene's end - BACK (default 0.6 s), for
                                             `npx hyperframes snapshot --at ...` (every element should be on screen)
  python3 scripts/tools.py check             run `hyperframes check` and print only errors/warnings, one line each
  python3 scripts/tools.py anchors           before the first build: every A()/at() phrase in scenes.py, checked
                                             against its scene window. Prints NOT FOUND, BEFORE (matched before the
                                             scene), AFTER (past its end) and LATE (in the last 0.8 s, where the exit
                                             animation starts). Silent = all good. Loop offsets like 0.15 * i are ignored
  python3 scripts/tools.py montage IMG... [--out research/montage.jpg] [--cols 3]
                                             one grid of screenshots (each fit into 640x400) to check content and
                                             privacy in a single Read
  python3 scripts/tools.py compare NEW.mp4 REF.mp4
                                             frame-by-frame SSIM (at 320x180, ~1 min for 7 min of video): mean,
                                             frames under 0.95 and the worst moments. Use it after changing the
                                             kit's render path: a timing bug shows as a low mean (in sync ~0.99+)
"""
import json, os, re, subprocess, sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))


def words():
    out = []
    for line in open(os.path.join(ROOT, 'research/words.txt')):
        a, b, w = line.rstrip('\n').split(' ', 2)
        out.append((float(a), w))
    return out


def scene_rows():
    rows = [l.split() for l in open(os.path.join(ROOT, 'scene-list.txt')) if l.strip()]
    html = open(os.path.join(ROOT, 'index.html')).read()
    total = float(re.search(r'data-composition-id="main" data-start="0" data-duration="([\d.]+)"', html).group(1))
    return [(r[0], float(r[1]), r[2]) for r in rows], total


def main(argv):
    cmd = argv[0] if argv else ''
    if cmd == 'words':
        lo, hi = (float(argv[1]), float(argv[2])) if len(argv) > 2 else (0, 1e9)
        ws = [(t, w) for t, w in words() if lo <= t <= hi]
        for i in range(0, len(ws), 14):
            print(' '.join('%.2f:%s' % x for x in ws[i:i + 14]))
    elif cmd == 'plan':
        ws, n, cur = words(), 0, []
        for t, w in ws:
            cur.append((t, w))
            if re.search(r'[.?!]["\')]*$', w) or len(cur) >= 28:
                n += 1
                print('%3d %7.2f  %s' % (n, cur[0][0], ' '.join(x for _, x in cur)))
                cur = []
        if cur:
            print('%3d %7.2f  %s' % (n + 1, cur[0][0], ' '.join(x for _, x in cur)))
    elif cmd == 'find':
        norm = lambda s: re.sub(r"[^a-z0-9%$]", '', s.lower())
        ws = [(t, part) for t, w in words() for part in w.split('-') if part]   # "self-promo" matches "self"
        nw = [norm(w) for _, w in ws]
        for phrase in argv[1:]:
            q = [norm(x) for x in phrase.replace('-', ' ').split()]
            hits = [i for i in range(len(ws) - len(q) + 1) if nw[i:i + len(q)] == q]
            if len(argv) == 2:
                for i in hits:
                    print('%.2f  %s' % (ws[i][0], ' '.join(w for _, w in ws[max(0, i - 4):i + len(q) + 6])))
            else:
                print('%-28s %s' % (phrase + ':', ' '.join('%.2f' % ws[i][0] for i in hits[:8]) or 'NOT FOUND'))
    elif cmd == 'share':
        rows, total = scene_rows()
        f = b = 0.0
        for i, (sid, t0, name) in enumerate(rows):
            d = (rows[i + 1][1] if i + 1 < len(rows) else total) - t0
            if name == 'FACE':
                f += d
            else:
                b += d
            if 0 < d < 2.3:
                print('short scene: %s %s %.2fs' % (sid, name, d))
        print('face %.1fs  b-roll %.1fs  b-roll %.0f%%' % (f, b, 100 * b / (f + b)))
    elif cmd == 'snaptimes':
        back = float(argv[1]) if len(argv) > 1 else 0.6
        rows, total = scene_rows()
        ts = ['%.2f' % ((rows[i + 1][1] if i + 1 < len(rows) else total) - back)
              for i, (_, _, name) in enumerate(rows) if name != 'FACE']
        print(','.join(ts))
    elif cmd == 'check':
        r = subprocess.run(['npx', 'hyperframes', 'check', '--json'], cwd=ROOT, capture_output=True, text=True)
        d = json.loads(r.stdout)
        print('ok', d.get('ok'))
        for k, v in d.items():
            if isinstance(v, dict) and 'findings' in v:
                for f in v['findings']:
                    if f.get('severity') in ('error', 'warning'):
                        print('%-8s %-22s %8s %-16s %s' % (f['severity'], f.get('code'), f.get('time', ''),
                                                          f.get('selector', ''), (f.get('message') or '')[:110]))
    elif cmd == 'anchors':
        import inspect
        sys.path.insert(0, os.path.join(ROOT, 'scripts'))
        from plumbing.anchors import load, T
        load(os.path.join(ROOT, 'research/words.txt'))
        import scenes
        b = scenes.board()
        end = words()[-1][0] + 1.0
        pat = re.compile(r"""\b(A|at)\(c, (["'])(.+?)\2(?:, (-?[\d.]+)\))?""")
        for i, (t0, fn, kw) in enumerate(b):
            if fn is None or getattr(fn, '__module__', '') != 'scenes':
                continue
            t1 = b[i + 1][0] if i + 1 < len(b) else end
            for m in pat.finditer(inspect.getsource(fn)):
                kind, ph, off = m.group(1), m.group(3), float(m.group(4) or 0)
                try:
                    tt = T(ph, after=t0 - 0.6)
                except Exception:
                    tt = None
                tag = '%-14s [%7.2f-%7.2f] %-30r' % (fn.__name__, t0, t1, ph)
                if tt is None:
                    print(tag, 'NOT FOUND in this scene or later')
                    continue
                el = tt + off if kind == 'A' else max(tt + off, t0 + 0.5)
                if tt > t1:
                    print(tag, 'AFTER: matched at %.2f, past the scene end' % tt)
                elif el > t1 - 0.8:
                    print(tag, 'LATE: lands at %.2f, %.2f s before the end (needs >= 0.8)' % (el, t1 - el))
                elif tt < t0 - 0.6:
                    print(tag, 'BEFORE: matched at %.2f' % tt)
    elif cmd == 'montage':
        args, out, cols = argv[1:], os.path.join(ROOT, 'research/montage.jpg'), 3
        if '--out' in args:
            j = args.index('--out'); out = args[j + 1]; del args[j:j + 2]
        if '--cols' in args:
            j = args.index('--cols'); cols = int(args[j + 1]); del args[j:j + 2]
        n = len(args)
        cols = max(1, min(cols, n))
        ins, chains, lay = [], '', []
        for k, a in enumerate(args):
            ins += ['-i', a]
            chains += '[%d]scale=640:400:force_original_aspect_ratio=decrease,pad=640:400:-1:-1:white[v%d];' % (k, k)
            lay.append('%d_%d' % (640 * (k % cols), 400 * (k // cols)))
        if n == 1:
            fc = chains + '[v0]null'
        else:
            fc = chains + ''.join('[v%d]' % k for k in range(n)) + 'xstack=inputs=%d:layout=%s:fill=white' % (n, '|'.join(lay))
        subprocess.run(['ffmpeg', '-v', 'error', '-y'] + ins + ['-filter_complex', fc, '-frames:v', '1', out], check=True)
        print(out)
    elif cmd == 'compare':
        import tempfile
        log = tempfile.mktemp(suffix='.ssim')
        subprocess.run(['ffmpeg', '-nostats', '-v', 'error', '-i', argv[1], '-i', argv[2], '-lavfi',
                        '[0:v]scale=320:180[a];[1:v]scale=320:180[b];[a][b]ssim=stats_file=%s' % log, '-f', 'null', '-'],
                       check=True)
        rows = []
        for l in open(log):
            d = dict(x.split(':') for x in l.split() if ':' in x)
            rows.append((float(d['All']), (int(d['n']) - 1) / 30))
        os.remove(log)
        print('frames %d  mean SSIM %.4f  under 0.95: %d  under 0.9: %d' % (
            len(rows), sum(r[0] for r in rows) / len(rows), sum(r[0] < 0.95 for r in rows), sum(r[0] < 0.9 for r in rows)))
        seen = []
        for v, t in sorted(rows):
            if all(abs(t - x) > 1.0 for x in seen):
                seen.append(t)
                print('  %.3f at %.2fs' % (v, t))
            if len(seen) >= 6:
                break
    else:
        print(__doc__)


if __name__ == '__main__':
    main(sys.argv[1:])

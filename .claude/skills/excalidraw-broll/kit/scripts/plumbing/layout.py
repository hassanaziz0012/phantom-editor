#!/usr/bin/env python3
"""Layout check from the real browser: where every element of every B-roll scene actually lands.

Run from the broll/ folder, after a build:
  python3 scripts/plumbing/layout.py            all scenes
  python3 scripts/plumbing/layout.py s05 s12    only these scenes

Each scene is opened on its own (no video, a few seconds for the whole film), its timeline is stepped
every 0.5 s, and the position of every text, image, logo and shape is read from the page. Prints one
line per problem:
  off_canvas     part of an element is outside the 1920x1080 frame
  outside_safe   text, image or logo outside the safe area (x 96-1824, y 54-1026)
  text_overlap   two texts are on top of each other at the same moment
  text_on_image  a text sits on a screenshot (fine if it is a callout you placed there on purpose)
  logo_overlap   a logo overlaps a text, an image or another logo
  label_spills   a k.box label is bigger than its box
  crosses_box    a text crosses the edge of a rect / ellipse / box (half in, half out)
  small_text     text smaller than 28 px
  never_shown    an element that is never fully visible before the scene exits (anchor too late, or past the end?)
  late           an element that starts in the last 0.8 s of the scene
  under_face     an element reaches into the bottom-right corner that the face square covers (x > 1480 and
                 y > 640). Move it, or give the scene face=False in board() if it really needs the full canvas.
Exit code 1 when there is an off_canvas, text_overlap, label_spills, never_shown or under_face.
"""
import asyncio, json, os, re, sys

from playwright.async_api import async_playwright

ROOT = os.getcwd()
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
from sketch import PIP_CLEAR  # noqa: E402
W, H = 1920, 1080
SAFE = (96, 54, 1824, 1026)
HARD = ('off_canvas', 'text_overlap', 'label_spills', 'never_shown', 'under_face')
FACE_BOX = [PIP_CLEAR[0], PIP_CLEAR[1], W, H]   # where the face square sits, plus its gap

# Runs in the page. Returns every item under the scene's .cam with its box, relative to the canvas.
PROBE = r"""
(sid) => {
  const cam = document.getElementById(sid + '-cam');
  if (!cam) return null;
  const strip = id => (id || '').replace(sid + '-', '');
  const num = v => parseFloat(v) || 0;
  // how much of an item is revealed: opacity chain x the part of its box left by clip-path insets
  const shown = (el, box) => {
    let op = 1, c = [-1e9, -1e9, 1e9, 1e9];
    for (let e = el; e && e !== cam.parentElement; e = e.parentElement) {
      const cs = getComputedStyle(e);
      op *= num(cs.opacity === '' ? 1 : cs.opacity);
      const m = /inset\(([^)]*)\)/.exec(cs.clipPath || '');
      if (m) {
        const r = e.getBoundingClientRect();
        const p = m[1].split(/\s+/).filter(x => x !== 'round');
        const v = (s, dim) => s.endsWith('%') ? num(s) / 100 * dim : num(s);
        const t = p[0] || '0', ri = p[1] || t, b = p[2] || t, l = p[3] || ri;
        c = [Math.max(c[0], r.left + v(l, r.width)), Math.max(c[1], r.top + v(t, r.height)),
             Math.min(c[2], r.right - v(ri, r.width)), Math.min(c[3], r.bottom - v(b, r.height))];
      }
    }
    if (!box) { const r = el.getBoundingClientRect(); box = [r.left, r.top, r.right, r.bottom]; }
    const a = (box[2] - box[0]) * (box[3] - box[1]);
    const w = Math.min(box[2], c[2]) - Math.max(box[0], c[0]), h = Math.min(box[3], c[3]) - Math.max(box[1], c[1]);
    return op * (a > 0 ? Math.max(0, w) * Math.max(0, h) / a : 0);
  };
  const drawn = svg => {   // rough shapes draw on with stroke-dashoffset; fills fade in
    const p = svg.querySelector('.o path');
    if (!p) return 1;
    const arr = num(getComputedStyle(p).strokeDasharray), off = num(getComputedStyle(p).strokeDashoffset);
    return arr ? Math.max(0, Math.min(1, 1 - off / arr)) : 1;
  };
  // visible box after overflow:hidden ancestors
  const clipBox = (r, el) => {
    let b = {x0: r.left, y0: r.top, x1: r.right, y1: r.bottom};
    for (let e = el.parentElement; e && e !== cam; e = e.parentElement) {
      if (getComputedStyle(e).overflow !== 'visible' && !e.classList.contains('sv')) {
        const c = e.getBoundingClientRect();
        b = {x0: Math.max(b.x0, c.left), y0: Math.max(b.y0, c.top), x1: Math.min(b.x1, c.right), y1: Math.min(b.y1, c.bottom)};
      }
    }
    return b.x1 > b.x0 && b.y1 > b.y0 ? [b.x0, b.y0, b.x1, b.y1].map(v => Math.round(v * 10) / 10) : null;
  };
  const owner = el => {
    const o = el.closest('.tx,.bx,.im,.lg');
    if (o && cam.contains(o)) return o;
    let e = el;
    while (e.parentElement && e.parentElement !== cam) e = e.parentElement;
    return e;
  };
  const out = [];
  const tw = document.createTreeWalker(cam, NodeFilter.SHOW_TEXT);
  const texts = new Map();
  for (let n = tw.nextNode(); n; n = tw.nextNode()) {
    if (!n.nodeValue.trim() || n.parentElement.closest('svg,script,style')) continue;
    const o = owner(n.parentElement);
    if (!texts.has(o)) texts.set(o, []);
    texts.get(o).push(n);
  }
  for (const [o, nodes] of texts) {
    const lines = [];
    let size = 1e9;
    for (const n of nodes) {
      const range = document.createRange();
      range.selectNodeContents(n);
      const sc = n.parentElement.getBoundingClientRect().width / (n.parentElement.offsetWidth || 1) || 1;
      size = Math.min(size, num(getComputedStyle(n.parentElement).fontSize) * sc);
      for (const r of range.getClientRects()) {
        const b = clipBox(r, n.parentElement);
        if (b && b[2] - b[0] > 1) lines.push(b);
      }
    }
    if (!lines.length) continue;
    const box = [Math.min(...lines.map(l => l[0])), Math.min(...lines.map(l => l[1])),
                 Math.max(...lines.map(l => l[2])), Math.max(...lines.map(l => l[3]))];
    const it = {kind: 'text', id: strip(o.id) || '?', box, lines, shown: shown(o, box), size: Math.round(size),
                txt: o.textContent.trim().replace(/\s+/g, ' ').slice(0, 28)};
    if (o.classList.contains('bx')) {
      const r = o.getBoundingClientRect();
      it.host = [r.left, r.top, r.right, r.bottom];
      const lb = o.querySelector('.lb');
      if (lb) it.shown = shown(lb, box);
    }
    out.push(it);
  }
  for (const el of cam.querySelectorAll('.im, .lg')) {
    const b = clipBox(el.getBoundingClientRect(), el);
    if (b) out.push({kind: el.classList.contains('im') ? 'image' : 'logo', id: strip(el.id) || '?', box: b, shown: shown(el, b)});
  }
  for (const el of cam.querySelectorAll('img')) {
    if (el.closest('.im,.lg')) continue;
    const b = clipBox(el.getBoundingClientRect(), el);
    if (b) out.push({kind: 'image', id: strip(owner(el).id) || '?', box: b, shown: shown(el, b)});
  }
  for (const svg of cam.querySelectorAll('svg')) {
    if (svg.closest('.lg')) continue;
    const g = svg.querySelector('.o') ? svg.querySelectorAll('.o, .f') : [svg];
    let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
    for (const e of g) {
      const r = e.getBoundingClientRect();
      if (!r.width && !r.height) continue;
      x0 = Math.min(x0, r.left); y0 = Math.min(y0, r.top); x1 = Math.max(x1, r.right); y1 = Math.max(y1, r.bottom);
    }
    if (x1 < x0) continue;
    const b = clipBox({left: x0, top: y0, right: x1, bottom: y1}, svg);
    const host = svg.closest('.bx');
    if (b) out.push({kind: 'shape', id: strip((host || svg).id) || strip(owner(svg).id) || '?', box: b,
                     shape: host ? 'box' : (svg.dataset.k || ''), shown: Math.min(shown(svg, b), drawn(svg))});
  }
  return out;
}
"""


def scenes():
    if not os.path.exists(os.path.join(ROOT, 'scene-list.txt')):
        sys.exit('no scene-list.txt in %s: run this from the broll/ folder, after python3 scripts/build.py' % ROOT)
    rows = [l.split() for l in open(os.path.join(ROOT, 'scene-list.txt')) if l.strip()]
    html = open(os.path.join(ROOT, 'index.html')).read()
    total = float(re.search(r'data-composition-id="main" data-start="0" data-duration="([\d.]+)"', html).group(1))
    gsap = re.search(r'<script src="([^"]*gsap[^"]*)"', html).group(1)
    out = []
    for i, r in enumerate(rows):
        if r[2] != 'FACE':
            out.append((r[0], r[2], float(r[1]), (float(rows[i + 1][1]) if i + 1 < len(rows) else total) - float(r[1]),
                        'noface' not in r[3:]))
    return out, gsap


def page_html(sid, gsap):
    tpl = open(os.path.join(ROOT, 'compositions/%s.html' % sid)).read()
    body = re.search(r'<template>(.*)</template>', tpl, re.S).group(1)
    return ('<!doctype html><html><head><meta charset="utf-8"><base href="file://%s/">'
            '<style>*{margin:0;padding:0;box-sizing:border-box}html,body{width:%dpx;height:%dpx;overflow:hidden}</style>'
            '<script src="%s"></script><script>window.__timelines={}</script></head><body>%s</body></html>'
            % (ROOT, W, H, gsap, body))


def inter(a, b):
    w, h = min(a[2], b[2]) - max(a[0], b[0]), min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 0 and h > 0 else 0.0


def area(a):
    return max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])


def ink(l):
    """A text line box without its line-height padding."""
    p = (l[3] - l[1]) * 0.18
    return [l[0], l[1] + p, l[2], l[3] - p]


def fmt(b):
    return '(%d,%d %dx%d)' % (b[0], b[1], b[2] - b[0], b[3] - b[1])


def analyse(samples, dur, face=False):
    """samples: [(t, items)] -> {(code, ids): message}, keeping the first time each problem is seen.
    face: the scene shows the face square, so nothing may reach into FACE_BOX."""
    found = {}

    def add(code, ids, msg, t):
        found.setdefault((code, ids), '%s  @%.1fs' % (msg, t))

    seen, begun, kinds = set(), set(), {}
    for t, items in samples:
        vis = [it for it in items if it['shown'] > 0.85] if t <= dur - 0.55 else []
        for it in items:
            kinds[(it['kind'], it['id'])] = it
            if it['shown'] > 0.5:
                seen.add((it['kind'], it['id']))
            if it['shown'] > 0.03 and t <= dur - 0.8:
                begun.add((it['kind'], it['id']))
        for it in vis:
            b, name = it['box'], '%s %s' % (it['kind'], it['id'])
            label = ' "%s"' % it['txt'] if it['kind'] == 'text' else ''
            if b[0] < -2 or b[1] < -2 or b[2] > W + 2 or b[3] > H + 2:
                add('off_canvas', (name,), '%s%s %s leaves the 1920x1080 frame' % (name, label, fmt(b)), t)
            elif it['kind'] != 'shape' and (b[0] < SAFE[0] - 4 or b[1] < SAFE[1] - 4 or b[2] > SAFE[2] + 4 or b[3] > SAFE[3] + 4):
                side = ', '.join(s for s, bad in (('left', b[0] < SAFE[0] - 4), ('top', b[1] < SAFE[1] - 4),
                                                  ('right', b[2] > SAFE[2] + 4), ('bottom', b[3] > SAFE[3] + 4)) if bad)
                add('outside_safe', (name,), '%s%s %s past the safe area: %s' % (name, label, fmt(b), side), t)
            if face:
                fw, fh = min(b[2], W) - max(b[0], FACE_BOX[0]), min(b[3], H) - max(b[1], FACE_BOX[1])
                if fw > 8 and fh > 8:
                    add('under_face', (name,), '%s%s %s reaches into the face square\'s corner (x > %d, y > %d)'
                        % (name, label, fmt(b), FACE_BOX[0], FACE_BOX[1]), t)
            if it['kind'] == 'text':
                if it['size'] < 28:
                    add('small_text', (name,), '%s%s is %d px' % (name, label, it['size']), t)
                h = it.get('host')
                if h and (b[0] < h[0] - 2 or b[1] < h[1] - 2 or b[2] > h[2] + 2 or b[3] > h[3] + 2):
                    add('label_spills', (name,), 'box %s: label%s %s is bigger than the box %s' % (it['id'], label, fmt(b), fmt(h)), t)
        for i, a in enumerate(vis):
            for b in vis[i + 1:]:
                if a['id'] == b['id']:
                    continue
                ka, kb = a['kind'], b['kind']
                pair = tuple(sorted(('%s %s' % (ka, a['id']), '%s %s' % (kb, b['id']))))
                if ka == kb == 'text':
                    ov = max(inter(ink(x), ink(y)) / max(1.0, min(area(ink(x)), area(ink(y)))) for x in a['lines'] for y in b['lines'])
                    if ov > 0.12:
                        add('text_overlap', pair, 'text %s "%s" and text %s "%s" overlap' % (a['id'], a['txt'], b['id'], b['txt']), t)
                    continue
                tx, other = (a, b) if ka == 'text' else (b, a) if kb == 'text' else (None, None)
                if tx is not None and other['kind'] == 'image':
                    if sum(inter(ink(l), other['box']) for l in tx['lines']) / max(1.0, sum(area(ink(l)) for l in tx['lines'])) > 0.25:
                        add('text_on_image', pair, 'text %s "%s" sits on image %s' % (tx['id'], tx['txt'], other['id']), t)
                elif tx is not None and other['kind'] == 'shape' and other.get('shape') in ('rect', 'ellipse', 'box'):
                    s = other['box']
                    for l in tx['lines']:
                        l = ink(l)
                        frac = inter(l, s) / max(1.0, area(l))
                        # half in, half out: not a label inside the shape and not a text next to it
                        if 0.2 < frac < 0.8 and (l[2] - l[0]) < (s[2] - s[0]) * 1.5:
                            add('crosses_box', pair, 'text %s "%s" %s crosses the edge of %s %s %s'
                                % (tx['id'], tx['txt'], fmt(l), other['shape'], other['id'], fmt(s)), t)
                            break
                elif 'logo' in (ka, kb) and 'shape' not in (ka, kb):
                    lg, o = (a, b) if ka == 'logo' else (b, a)
                    if inter(lg['box'], o['box']) / max(1.0, min(area(lg['box']), area(o['box']))) > 0.2 and o['kind'] != 'image':
                        add('logo_overlap', pair, 'logo %s overlaps %s %s' % (lg['id'], o['kind'], o['id']), t)
    for key, it in kinds.items():
        if it['kind'] == 'shape' and any(k[1] == key[1] and k[0] != 'shape' for k in kinds):
            continue
        name = '%s %s' % key
        label = ' "%s"' % it['txt'] if it['kind'] == 'text' else ''
        if key not in seen:
            add('never_shown', (name,), '%s%s is never fully visible before the scene exits' % (name, label), dur)
        elif key not in begun:
            add('late', (name,), '%s%s starts in the last 0.8 s of the scene' % (name, label), dur - 0.8)
    return found


async def main(only):
    sc, gsap = scenes()
    if only:
        sc = [s for s in sc if s[0] in only]
    bad = total = 0
    async with async_playwright() as pw:
        br = await pw.chromium.launch()
        page = await br.new_page(viewport={'width': W, 'height': H})
        for sid, name, t0, dur, face in sc:
            tmp = os.path.join(ROOT, 'research', '.layout-%s.html' % sid)
            open(tmp, 'w').write(page_html(sid, gsap))
            try:
                await page.goto('file://' + tmp, wait_until='load')
                await page.evaluate('document.fonts.ready.then(() => 1)')
                # every 0.5 s, then 0.85 s before the end (late elements), 0.6 s (what snap.sh shows) and
                # just before the exit animation, which starts 0.45 s before the end
                times = [round(0.7 + 0.5 * i, 2) for i in range(int(max(0, dur - 1.6) / 0.5) + 1)]
                times += [round(max(0.1, dur - x), 2) for x in (0.85, 0.6, 0.47)]
                samples = []
                for t in times:
                    await page.evaluate('([s, t]) => { window.__timelines[s].seek(t, false); }', [sid, t])
                    items = await page.evaluate(PROBE, sid)
                    if items is None:
                        break
                    samples.append((t, items))
            finally:
                os.remove(tmp)
            if not samples:
                print('%s %-18s could not be read (no #%s-cam)' % (sid, name, sid))
                continue
            found = analyse(samples, dur, face)
            for (code, _), msg in sorted(found.items(), key=lambda kv: (kv[0][0] not in HARD, kv[0])):
                print('%s %-18s %-13s %s' % (sid, name[:18], code, msg))
                total += 1
                bad += code in HARD
        await br.close()
    print('layout: %d scenes, %d findings (%d must fix)' % (len(sc), total, bad))
    return 1 if bad else 0


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] in ('-h', '--help'):
        print(__doc__)
        sys.exit(0)
    sys.exit(asyncio.run(main(set(sys.argv[1:]))))

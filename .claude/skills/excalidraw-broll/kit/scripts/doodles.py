"""Reusable drawn objects (vocabulary, not layouts). Every function takes the scene's Sk as `k`, a unique
`sid`, canvas coordinates and an ABSOLUTE time `t` (None = already there when the scene opens).
Compose them into new layouts per video; never paste a previous video's composition.
"""
import math

from plumbing.core import sfx
from sketch import INK, GREY, LGREY, RED, GREEN, ORANGE, YEL_BG, VIOLET, VIO_BG, CLAUDE, CLAUDE_BG


def browser(k, sid, x, y, w, h, t=None, url=None):
    """Empty sketched browser window (draw your own page inside). Content area starts at y + 52."""
    k.rect(sid, x, y, w, h, t=t, sw=3)
    k.line(sid + 'b', x, y + 52, x + w, y + 52, t=t and t + 0.3, sw=2.4)
    for i, col in enumerate(('#ff8787', '#ffd43b', '#69db7c')):
        k.ellipse('%sd%d' % (sid, i), x + 30 + i * 28, y + 26, 15, 15, t=t and t + 0.3, stroke=INK, sw=1.6, fill=col,
                  fs='solid')
    if url:
        k.text(sid + 'u', x + 130, y + 12, url, 28, t=t and t + 0.4, color=GREY, anim='fade')


def code_file(k, sid, x, y, w, h, t=None, title='app.py', seed=0):
    """A source file drawn as coloured code lines. Returns [(x, y, w)] per line so you can ring/strike lines."""
    k.rect(sid, x, y, w, h, t=t, stroke=INK, fill='#f8f9fa', fs='solid', sw=3)
    k.text(sid + 'n', x + 24, y + 16, title, 34, t=t, code=True, color=GREY, anim='fade')
    out, n = [], max(4, int((h - 120) / 56))
    for i in range(n):
        ind = ((i + seed) * 7 % 5) % 3
        ln = 120 + ((i + seed) * 89) % 230
        ly = y + 90 + i * ((h - 120) / n)
        lx = x + 30 + ind * 40
        lw = min(ln * w / 520, w - 60 - ind * 40)
        col = ('#1971c2', VIOLET, INK, GREEN)[(i + seed) % 4] if i % 3 else INK
        k.line('%sl%d' % (sid, i), lx, ly, lx + lw, ly, t=t and t + 0.2 + i * 0.03, stroke=col, sw=5, rough_=0.5,
               d=0.2)
        out.append((lx, ly, lw))
    return out


def file_icon(k, sid, x, y, w, h, label, t=None, stroke=GREEN, fill='#b2f2bb', size=60, code=True):
    """Document with a folded corner and a name on it (SKILL.md, outline.md, report.pdf...)."""
    f = min(w, h) * 0.22
    k.poly(sid, [(x, y), (x + w - f, y), (x + w, y + f), (x + w, y + h), (x, y + h)], closed=True, t=t, stroke=stroke,
           fill=fill, sw=3.5)
    k.poly(sid + 'f', [(x + w - f, y), (x + w - f, y + f), (x + w, y + f)], t=t and t + 0.35, stroke=stroke, sw=3)
    k.text(sid + 'l', x + w / 2, y + h * 0.38, label, size, t=t and t + 0.4, align='c', w=w, code=code)


def lightbulb(k, sid, cx, cy, t=None, color=ORANGE, fill=YEL_BG):
    k.ellipse(sid, cx, cy, 220, 240, t=t, stroke=color, fill=fill, sw=3.5)
    k.rect(sid + 'b', cx - 50, cy + 120, 100, 60, t=t and t + 0.25, stroke=color, sw=3)
    for i, (dx, dy) in enumerate(((-170, -60), (170, -60), (0, -190), (-130, -170), (130, -170))):
        k.line('%sr%d' % (sid, i), cx + dx * 0.8, cy + dy * 0.8, cx + dx, cy + dy, t=t and t + 0.35 + i * 0.05,
               stroke=color, sw=3.5, d=0.15)


def target(k, sid, cx, cy, t=None, color=RED, r=260):
    for i, f in enumerate((1.0, 0.65, 0.3)):
        k.ellipse('%s%d' % (sid, i), cx, cy, r * f, r * f, t=t and t + i * 0.1, stroke=color, sw=3, d=0.3)


def gauge(k, sid, gx, gy, r, t=None, to_deg=128, label=None):
    """Half-circle gauge; the needle swings from left to `to_deg` degrees at t."""
    arc = [(gx + r * math.cos(math.pi + i * math.pi / 20), gy + r * math.sin(math.pi + i * math.pi / 20))
           for i in range(21)]
    k.curve(sid, arc, t=t and t - 0.3, stroke=INK, sw=4, d=0.5)
    k.raw('<svg class="sv" viewBox="0 0 1920 1080"><g id="@@-%sn"><line x1="%d" y1="%d" x2="%d" y2="%d" stroke="%s" '
          'stroke-width="7" stroke-linecap="round"/></g><circle cx="%d" cy="%d" r="12" fill="%s"/></svg>'
          % (sid, gx, gy, gx - r + 40, gy, RED, gx, gy, INK))
    if t is not None:
        k.js("tl.fromTo('#@@-%sn',{rotation:0,svgOrigin:'%d %d'},{rotation:%d,svgOrigin:'%d %d',duration:1.0,"
             "ease:'power2.out'},%.3f);" % (sid, gx, gy, to_deg, gx, gy, k.L(t)))
    if label:
        k.text(sid + 'l', gx, gy + 50, label, 54, t=t and t + 0.2, align='c', w=r * 2.4)


def prompt(k, sid, x, y, w, h, text, t, until=None, size=56, title=None, keys=True):
    """Claude-style prompt box with `text` typed from t to `until` (default ~0.06 s/char) + key clicks."""
    k.rect(sid, x, y, w, h, stroke=CLAUDE, sw=4)
    if title:
        k.text(sid + 'h', x + 30, y + 20, title, 34, color=CLAUDE)
    dur = (until - t) if until else min(4.0, 0.06 * len(text))
    k.text(sid + 'p', x + 40, y + (80 if title else 40), text, size, t=t, d=dur, code=True)
    if keys:
        for i in range(max(1, int(dur / 0.3))):
            sfx('key-press', t + 0.1 + i * 0.3, 0.12)


def bubble(k, sid, x, y, w, h, text, t=None, size=40, stroke=CLAUDE, fill=CLAUDE_BG, code=True):
    """Chat bubble that pops in with a short message."""
    k.box(sid, x, y, w, h, text, size, t=t, code=code, stroke=stroke, fill=fill, fs='solid', anim='pop')
    if t is not None:
        sfx('pop-low', t + 0.1, 0.2)


def scroller(k, sid, x, y, w, h, scene_dur, n=16, speed=1500):
    """A window of placeholder cards that scrolls up for the whole scene (feels like an endless feed)."""
    cards = []
    for i in range(n):
        cy = i * 150
        g = k.g('rectangle', [10, cy + 10, w - 20, 128], stroke=INK, fill='#fff', fs='solid', sw=2.2)
        g += k.g('line', [40, cy + 52, 40 + 300 + (i * 61) % 220, cy + 52], stroke=INK, sw=5, rough_=0.6)
        g += k.g('line', [40, cy + 96, 40 + 200 + (i * 37) % 260, cy + 96], stroke=LGREY, sw=4, rough_=0.6)
        cards.append(g)
    k.raw('<div id="@@-%s" style="position:absolute;left:%dpx;top:%dpx;width:%dpx;height:%dpx;overflow:hidden">'
          '<svg id="@@-%sc" class="sv" style="width:%dpx;height:%dpx" viewBox="0 0 %d %d">%s</svg></div>'
          % (sid, x, y, w, h, sid, w, n * 150, w, n * 150, ''.join(cards)))
    k.rect(sid + 'f', x - 8, y - 8, w + 16, h + 16, stroke=INK, sw=3)
    k.js("tl.fromTo('#@@-%sc',{y:0},{y:%d,duration:%.2f,ease:'none'},0.1);" % (sid, -speed, scene_dur - 0.2))


def counter(k, sid, x, y, a, b, t, d=0.8, size=200, color=RED, prefix='', suffix='', align='l', w=None):
    """Big number counting a -> b at t (lands on the word). Text id is sid, counter span id sid + 'v'."""
    k.text(sid, x, y, '%s<span id="@@-%sv">%d</span>%s' % (prefix, sid, a, suffix), size, t=t - 0.05, color=color,
           anim='fade', d=0.15, align=align, w=w)
    k.js("COUNT('#@@-%sv',%.3f,%d,%d,%.2f,fmtInt,'power3.out');" % (sid, k.L(t - 0.05), a, b, d))


def sticky(k, sid, x, y, w, h, text, t=None, size=46, fill=YEL_BG, stroke=ORANGE):
    k.box(sid, x, y, w, h, text, size, t=t, fill=fill, fs='solid', stroke=stroke, anim='pop',
          lstyle='white-space:pre')
    if t is not None:
        sfx('pop-low', t + 0.1, 0.22)


def bars(k, sid, x, y, rows, t=None, max_w=1200, h=70, gap=50, label_w=180):
    """Horizontal comparison bars: rows = [(label, fraction 0..1, stroke, fill, time or None)]."""
    for i, (lab, frac, st, fl, tt) in enumerate(rows):
        yy = y + i * (h + gap)
        k.text('%sl%d' % (sid, i), x, yy + 10, lab, 44, t=tt, color=GREY)
        k.rect('%sb%d' % (sid, i), x + label_w, yy, max(30, max_w * frac), h, t=tt, stroke=st, fill=fl, sw=3, d=0.4)


def person(k, sid, cx, cy, s=1.0, t=None, color=INK, sign=None, sign_color=None, sign_fill=None, size=30, d=0.5):
    """Stick figure standing at (cx, cy) = hip centre; ~190*s tall. sign='TEXT' adds a placard held overhead."""
    g = k.g('ellipse', [cx, cy - 120 * s, 56 * s, 56 * s], stroke=color, sw=3, fill='#fff', fs='solid')
    g += k.g('line', [cx, cy - 92 * s, cx, cy], stroke=color, sw=3)
    g += k.g('line', [cx, cy, cx - 28 * s, cy + 70 * s], stroke=color, sw=3)
    g += k.g('line', [cx, cy, cx + 28 * s, cy + 70 * s], stroke=color, sw=3)
    if sign:
        g += k.g('line', [cx, cy - 70 * s, cx - 40 * s, cy - 150 * s], stroke=color, sw=3)
        g += k.g('line', [cx, cy - 70 * s, cx + 40 * s, cy - 150 * s], stroke=color, sw=3)
    else:
        g += k.g('line', [cx, cy - 70 * s, cx - 42 * s, cy - 20 * s], stroke=color, sw=3)
        g += k.g('line', [cx, cy - 70 * s, cx + 42 * s, cy - 20 * s], stroke=color, sw=3)
    k.svg(sid, g, t, d)
    if sign:
        w = max(110 * s, len(sign) * size * 0.75 + 44)
        k.box(sid + 's', cx - w / 2, cy - 150 * s - 70, w, 64, sign, size, t=t and t + 0.15,
              stroke=sign_color or color, fill=sign_fill or '#fff', fs='solid', anim='pop', sw=2.4,
              lstyle='white-space:nowrap')


def phone(k, sid, x, y, w, h, t=None, d=0.5):
    """Sketched phone outline; screen area is (x+18, y+60, w-36, h-110)."""
    g = k.g('rectangle', [x, y, w, h], stroke=INK, sw=3.5, rough_=0.8)
    g += k.g('line', [x + w / 2 - 40, y + 30, x + w / 2 + 40, y + 30], stroke=INK, sw=5, rough_=0.4)
    g += k.g('ellipse', [x + w / 2, y + h - 26, 30, 30], stroke=INK, sw=2.4, rough_=0.6)
    k.svg(sid, g, t, d)


def flag(k, sid, x, y, t=None, color=RED, fill='#ffc9c9', s=1.0, d=0.45):
    """Hand-drawn flag on a pole; pole foot at (x, y), cloth to the right, ~170*s tall."""
    g = k.g('line', [x, y, x, y - 170 * s], stroke=INK, sw=3.5)
    g += k.g('polygon', [[[x, y - 170 * s], [x + 95 * s, y - 145 * s], [x, y - 115 * s]]], stroke=color, sw=3,
             fill=fill, fs='solid')
    k.svg(sid, g, t, d)


def trash(k, sid, x, y, w=150, h=180, t=None, color=GREY, d=0.5):
    """Bin with lid; top-left of the lid at (x, y)."""
    g = k.g('line', [x - 10, y, x + w + 10, y], stroke=color, sw=4)
    g += k.g('line', [x + w * 0.35, y - 16, x + w * 0.65, y - 16], stroke=color, sw=4)
    g += k.g('polygon', [[[x + 6, y + 10], [x + w - 6, y + 10], [x + w - 22, y + h], [x + 22, y + h]]], stroke=color,
             sw=3, fill='#e9ecef', fs='hachure')
    for i in (0.33, 0.5, 0.67):
        g += k.g('line', [x + w * i, y + 34, x + w * i, y + h - 24], stroke=color, sw=2.4)
    k.svg(sid, g, t, d)


def clock(k, sid, cx, cy, r, t=None, color=INK, d=0.5):
    """Clock face with 12 ticks (no hands). Pair with pie() to shade minutes."""
    g = k.g('ellipse', [cx, cy, 2 * r, 2 * r], stroke=color, sw=4)
    for i in range(12):
        a = i * math.pi / 6
        r1 = r * (0.82 if i % 3 else 0.74)
        g += k.g('line', [cx + r1 * math.sin(a), cy - r1 * math.cos(a), cx + r * 0.92 * math.sin(a),
                          cy - r * 0.92 * math.cos(a)], stroke=color, sw=3 if i % 3 == 0 else 2)
    k.svg(sid, g, t, d)


def pie(k, sid, cx, cy, r, frac, t=None, color=RED, fill='#ffc9c9', d=0.6, start=0.0):
    """Hatched pie slice from `start` to `start+frac` of a full turn (clockwise from 12 o'clock)."""
    n = max(3, int(48 * frac))
    pts = [[cx, cy]] + [[cx + r * math.sin(2 * math.pi * (start + frac * i / n)),
                         cy - r * math.cos(2 * math.pi * (start + frac * i / n))] for i in range(n + 1)]
    k.svg(sid, k.g('polygon', [pts], stroke=color, sw=3, fill=fill, fs='hachure'), t, d)


def card(k, sid, x, y, w, h, t=None, stroke=INK, fill='#fff', fs='solid', seed=0, d=0.3):
    """Tiny post / job card: frame + dark title line + grey body line (line lengths vary with seed).
    Rows of these make feeds, piles and before/after lists; cross / check / ring them."""
    g = k.g('rectangle', [x, y, w, h], stroke=stroke, fill=fill, fs=fs, sw=2.4)
    g += k.g('line', [x + 14, y + h * 0.3, x + 14 + (w - 28) * (0.6 + 0.3 * ((seed * 37) % 10) / 10), y + h * 0.3],
             stroke=INK, sw=4, rough_=0.5)
    g += k.g('line', [x + 14, y + h * 0.62, x + 14 + (w - 28) * (0.4 + 0.4 * ((seed * 53) % 10) / 10), y + h * 0.62],
             stroke=LGREY, sw=3.5, rough_=0.5)
    k.svg(sid, g, t, d)

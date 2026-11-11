"""Excalidraw-style sketch kit for this video's B-roll scenes.

Shapes come from rough.js (scripts/node/rough_server.js, seeded, so builds are repeatable) and are
drawn on with stroke-dash animation; hachure fills fade in behind the outline. Text is Excalifont.

Every B-roll scene is a sheet of "paper" over the face footage:
  face -> B : the sheet slides in from the right        (whoosh)
  B -> face : the sheet slides out to the left          (low whoosh)
  B -> B    : the camera pans right along the whiteboard (short whoosh)

Unless a scene's board() entry has face=False, the user's face sits in a rounded square at the bottom
right (PIP, drawn by build.py over the scene), so the scene keeps that corner empty.

Usage inside a scene function (see the API list in SKILL.md):
    k = Sk(c, 'name', kw['prev_face'], kw['next_face'], tag=(3, 'Topic label'), face=kw.get('face', True))
    k.rect('box', x, y, w, h, t=T('word'), fill=BLUE_BG)
    k.text('lbl', x, y, 'hello', 48, t=T('hello'))
    return k.done()
All times passed to Sk are ABSOLUTE seconds (T(...) values); None means "already there".
"""
import json, os, subprocess, math, html as _html

from plumbing.core import sfx, path as root_path

HERE = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------- palette (Excalidraw light)
INK, GREY, LGREY, FAINT = '#1e1e1e', '#868e96', '#ced4da', '#e9ecef'
RED, RED_BG = '#e03131', '#ffc9c9'
GREEN, GREEN_BG = '#2f9e44', '#b2f2bb'
BLUE, BLUE_BG = '#1971c2', '#a5d8ff'
ORANGE, YEL_BG = '#f08c00', '#ffec99'
VIOLET, VIO_BG = '#6741d9', '#d0bfff'
CLAUDE, CLAUDE_BG = '#d97757', '#f9dccf'
PAPER = '#ffffff'

W, H = 1920, 1080

# Face picture-in-picture: build.py puts the user's face in a rounded square at the bottom right of every
# B-roll scene, unless its board() entry has face=False. Scenes that show it keep their content out of
# the corner x > PIP_CLEAR[0] and y > PIP_CLEAR[1] (layout.py reports anything there as under_face).
PIP = (1512, 672, 360, 360)          # x, y, w, h of the face square on the 1920x1080 canvas
PIP_CLEAR = (1480, 640)              # PIP plus a 32 px gap

# ---------------------------------------------------------------- rough.js bridge
_node = None


def rough(op, args, **o):
    global _node
    if _node is None:
        _node = subprocess.Popen(['node', os.path.join(HERE, 'node', 'rough_server.js')], cwd=os.path.join(HERE, 'node'),
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    _node.stdin.write(json.dumps({'op': op, 'args': args, 'o': o}) + '\n')
    _node.stdin.flush()
    out = json.loads(_node.stdout.readline())
    if isinstance(out, dict) and 'error' in out:
        raise RuntimeError(out['error'])
    return out


def esc(s):
    return _html.escape(s, quote=False)


def est_w(s, size):
    """Rough Excalifont text width (calibrated by eye on snapshots)."""
    return len(s) * size * 0.5


# ---------------------------------------------------------------- shared css / js (injected via setup)
FONT_CSS = """
@font-face{font-family:'Excali';src:url('assets/fonts/excalifont.woff2') format('woff2');font-display:block}
@font-face{font-family:'Shanns';src:url('assets/fonts/comicshanns.woff2') format('woff2');font-display:block}
#root{background:transparent}
.paper{position:absolute;inset:0;background:#fff;overflow:hidden;box-shadow:0 0 70px rgba(0,0,0,.35)}
.grid{position:absolute;top:0;left:-960px;width:3840px;height:1080px;
  background-image:radial-gradient(circle,#d5d8dc 1.7px,transparent 2px);background-size:40px 40px;background-position:20px 20px}
.cam{position:absolute;inset:0}
.sv{position:absolute;left:0;top:0;width:1920px;height:1080px;overflow:visible;pointer-events:none}
.tx{position:absolute;font-family:'Excali',sans-serif;color:#1e1e1e;white-space:pre;line-height:1.18}
.tx.code{font-family:'Shanns',monospace}
.bx{position:absolute;display:flex;align-items:center;justify-content:center;text-align:center;font-family:'Excali',sans-serif;line-height:1.15;color:#1e1e1e}
.bx>.sv{left:0;top:0}
.bx>.lb{position:relative;padding:0 18px}
.im{position:absolute;overflow:hidden;background:#fff}
.im img{position:absolute;left:0;top:0;width:100%;height:100%;object-fit:cover;object-position:top left;transform-origin:0 0}
.lg{position:absolute}
.lg svg{width:100%;height:100%;display:block}
"""

SK_JS = r"""
const SKD=(s,t,d=0.5,fd=null)=>{DRAW(s+' .o path',t,d,'power1.inOut');if(Q(s+' .f').length)tl.fromTo(s+' .f',{opacity:0},{opacity:1,duration:fd??Math.max(0.25,d*0.7),ease:'power1.out'},t+d*0.45)};
const WRITE=(s,t,d=0.6)=>tl.fromTo(s,{clipPath:'inset(-30% 101% -30% -3%)'},{clipPath:'inset(-30% -3% -30% -3%)',duration:d,ease:'power1.inOut'},t);
const FADE=(s,t,d=0.35)=>tl.fromTo(s,{opacity:0},{opacity:1,duration:d,ease:'power1.out'},t);
const POPIN=(s,t,o={})=>tl.fromTo(s,{opacity:0,scale:o.s??0.6,rotation:o.r??0},{opacity:1,scale:1,rotation:0,duration:o.d??0.5,ease:o.e??'back.out(1.7)',stagger:o.st??0},t);
const RISE=(s,t,o={})=>tl.fromTo(s,{opacity:0,y:o.y??30,filter:'blur(6px)'},{opacity:1,y:0,filter:'blur(0px)',duration:o.d??0.5,ease:'expo.out',stagger:o.st??0},t);
"""


# ---------------------------------------------------------------- the per-scene builder
def png_size(p):
    """(width, height) of a PNG from its header (no PIL needed)."""
    with open(p, 'rb') as f:
        head = f.read(24)
    if head[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('%s is not a PNG' % p)
    import struct
    return struct.unpack('>II', head[16:24])


class Shot:
    """A placed screenshot: maps image pixels and shoot.py's measured boxes to canvas coordinates."""
    def __init__(self, src, x, y, scale, boxes, w, h):
        self.src, self.x, self.y, self.s, self.boxes, self.w, self.h = src, x, y, scale, boxes, w, h

    def pt(self, px, py):
        return round(self.x + px * self.s), round(self.y + py * self.s)

    def r(self, key, pad=8):
        b = self.boxes.get(key)
        if not b:
            b = next((v for k_, v in self.boxes.items() if v and k_.startswith(key)), None)
        if not b:
            raise KeyError('%s: no measured box %r (have %s); add it to "texts" in the shoot.py targets'
                           % (self.src, key, sorted(self.boxes)))
        x, y = self.pt(b[0], b[1])
        return x - pad, y - pad, round(b[2] * self.s) + 2 * pad, round(b[3] * self.s) + 2 * pad


class Sk:
    _whoosh = 0

    def __init__(self, c, name, prev_face=False, next_face=False, tag=None, face=True):
        self.c, self.name = c, name
        self.prev_face, self.next_face = prev_face, next_face
        self.face = face   # the face square covers the bottom-right corner (see PIP_CLEAR)
        self.seed = 1 + sum(ord(ch) for ch in name) * 37
        self.parts, self.jsl, self.cssl = [], [], []
        if tag:
            self._tag(*tag)

    # -- time
    def L(self, t):
        return self.c.L(t)

    def _seed(self):
        self.seed += 7
        return self.seed

    # -- low level: svg group for one rough shape
    def g(self, op, args, stroke=INK, fill=None, fs='hachure', sw=2.6, rough_=1.1, bow=1.0, gap=None, fw=None,
          angle=-41, dash=None):
        o = dict(seed=self._seed(), stroke=stroke, strokeWidth=sw, roughness=rough_, bowing=bow,
                 preserveVertices=False)
        if fill:
            o.update(fill=fill, fillStyle=fs, hachureAngle=angle, hachureGap=gap or 9, fillWeight=fw or 2.2)
        if dash:
            o.update(strokeLineDash=dash)
        paths = rough(op, args, **o)
        fills, outs = [], []
        for p in paths:
            if p['stroke'] == 'none' or (p['fill'] not in (None, 'none') and p['stroke'] in (None, 'none')):
                fills.append('<path d="%s" fill="%s" stroke="none"/>' % (p['d'], p['fill']))
            elif p['stroke'] == stroke and p['fill'] in (None, 'none') and abs(p['sw'] - sw) < 1e-6:
                outs.append('<path d="%s" fill="none" stroke="%s" stroke-width="%s" stroke-linecap="round" stroke-linejoin="round"%s/>'
                            % (p['d'], p['stroke'], p['sw'], ' stroke-dasharray="%s"' % ' '.join(map(str, dash)) if dash else ''))
            else:  # hachure / zigzag fill lines
                fills.append('<path d="%s" fill="%s" stroke="%s" stroke-width="%s" stroke-linecap="round"/>'
                             % (p['d'], p['fill'] or 'none', p['stroke'], p['sw']))
        return ('<g class="f">%s</g>' % ''.join(fills) if fills else '') + '<g class="o">%s</g>' % ''.join(outs)

    def svg(self, sid, inner, t=None, d=0.5, extra=''):
        """Full-canvas svg holding `inner` (one or more g() groups)."""
        self.parts.append('<svg id="@@-%s" class="sv" viewBox="0 0 1920 1080"%s>%s</svg>' % (sid, extra, inner))
        if t is not None:
            self.jsl.append("SKD('#@@-%s',%.3f,%.3f);" % (sid, self.L(t), d))
        return sid

    # -- shapes (x, y in canvas px)
    def rect(self, sid, x, y, w, h, t=None, d=0.5, **kw):
        return self.svg(sid, self.g('rectangle', [x, y, w, h], **kw), t, d, ' data-k="rect"')   # data-k: layout.py

    def ellipse(self, sid, cx, cy, w, h, t=None, d=0.5, k_='ellipse', **kw):
        return self.svg(sid, self.g('ellipse', [cx, cy, w, h], **kw), t, d, ' data-k="%s"' % k_)

    def line(self, sid, x1, y1, x2, y2, t=None, d=0.35, **kw):
        return self.svg(sid, self.g('line', [x1, y1, x2, y2], **kw), t, d)

    def poly(self, sid, pts, t=None, d=0.5, closed=False, **kw):
        return self.svg(sid, self.g('polygon' if closed else 'linearPath', [[list(p) for p in pts]], **kw), t, d)

    def curve(self, sid, pts, t=None, d=0.5, **kw):
        return self.svg(sid, self.g('curve', [[list(p) for p in pts]], **kw), t, d)

    def _head(self, p_from, p_to, size, kw):
        ang = math.atan2(p_to[1] - p_from[1], p_to[0] - p_from[0])
        a1, a2 = ang + math.radians(152), ang - math.radians(152)
        x, y = p_to
        return self.g('linearPath', [[[x + size * math.cos(a1), y + size * math.sin(a1)], [x, y],
                                      [x + size * math.cos(a2), y + size * math.sin(a2)]]],
                      **{k: v for k, v in kw.items() if k not in ('fill',)})

    def arrow(self, sid, pts, t=None, d=0.45, head=24, curved=None, both=False, **kw):
        """Arrow through pts; curved=True uses a rough curve through all points."""
        curved = len(pts) > 2 if curved is None else curved
        shaft = self.g('curve' if curved else 'linearPath', [[list(p) for p in pts]], **kw)
        # direction of the last bit: for curves sample near the end
        hd = self._head(pts[-2], pts[-1], head, kw)
        tail = self._head(pts[1], pts[0], head, kw) if both else ''
        self.parts.append('<svg id="@@-%s" class="sv" viewBox="0 0 1920 1080"><g id="@@-%s-s">%s</g><g id="@@-%s-h">%s%s</g></svg>'
                          % (sid, sid, shaft, sid, hd, tail))
        if t is not None:
            self.jsl.append("DRAW('#@@-%s-s .o path',%.3f,%.3f,'power1.inOut');DRAW('#@@-%s-h .o path',%.3f,0.16,'power1.out');"
                            % (sid, self.L(t), d, sid, self.L(t) + d - 0.02))
        return sid

    def check(self, sid, x, y, s=1.0, t=None, color=GREEN, sw=4.5, snd=True):
        pts = [[x - 20 * s, y], [x - 5 * s, y + 17 * s], [x + 24 * s, y - 22 * s]]
        self.svg(sid, self.g('linearPath', [pts], stroke=color, sw=sw, rough_=0.7), t, 0.22)
        if t is not None and snd:
            sfx('click-soft', t + 0.12, 0.3)
        return sid

    def cross(self, sid, x, y, s=1.0, t=None, color=RED, sw=4.5, snd=True):
        a = self.g('line', [x - 20 * s, y - 20 * s, x + 20 * s, y + 20 * s], stroke=color, sw=sw, rough_=0.8)
        b = self.g('line', [x + 20 * s, y - 20 * s, x - 20 * s, y + 20 * s], stroke=color, sw=sw, rough_=0.8)
        self.parts.append('<svg id="@@-%s" class="sv" viewBox="0 0 1920 1080"><g id="@@-%s-a">%s</g><g id="@@-%s-b">%s</g></svg>'
                          % (sid, sid, a, sid, b))
        if t is not None:
            self.jsl.append("DRAW('#@@-%s-a .o path',%.3f,0.14);DRAW('#@@-%s-b .o path',%.3f,0.14);"
                            % (sid, self.L(t), sid, self.L(t) + 0.13))
            if snd:
                sfx('pop-low', t + 0.1, 0.22)
        return sid

    def strike(self, sid, x, y, w, t=None, color=RED, sw=4):
        return self.line(sid, x - 8, y + 4, x + w + 8, y - 4, t, 0.3, stroke=color, sw=sw, rough_=1.4)

    def underline(self, sid, x, y, w, t=None, color=ORANGE, sw=5, d=0.35):
        return self.curve(sid, [[x, y], [x + w * 0.5, y + 6], [x + w, y - 2]], t, d, stroke=color, sw=sw, rough_=1.2)

    def ring(self, sid, x, y, w, h, t=None, color=RED, sw=4, d=0.5):
        """Hand circle around a box (x, y, w, h)."""
        return self.ellipse(sid, x + w / 2, y + h / 2, w * 1.18 + 30, h * 1.35 + 26, t, d, k_='ring', stroke=color, sw=sw, rough_=1.6)

    def mark(self, sid, x, y, w, h, t=None, color=YEL_BG, d=0.4):
        """Highlighter behind text: solid rough fill, wiped in."""
        inner = self.g('rectangle', [x, y, w, h], stroke='none', fill=color, fs='solid', rough_=1.4)
        self.parts.append('<svg id="@@-%s" class="sv" viewBox="0 0 1920 1080" style="opacity:.85">%s</svg>' % (sid, inner))
        if t is not None:
            self.jsl.append("tl.fromTo('#@@-%s',{clipPath:'inset(0 %.1fpx 0 %.1fpx)'},{clipPath:'inset(0 %.1fpx 0 %.1fpx)',duration:%.2f,ease:'power2.inOut'},%.3f);"
                            % (sid, W - x + 4, x - 6, W - x - w - 10, x - 6, d, self.L(t)))
        return sid

    # -- text
    def text(self, sid, x, y, s, size=48, t=None, color=INK, w=None, align='l', anim='write', d=None, code=False,
             style='', cls=''):
        """x, y = top-left (align l), top-centre (c) or top-right (r)."""
        css = 'top:%dpx;font-size:%dpx;color:%s;' % (y, size, color)
        if align == 'c':
            w = w or 1600
            css += 'left:%dpx;width:%dpx;text-align:center;' % (x - w / 2, w)
        elif align == 'r':
            w = w or 1600
            css += 'left:%dpx;width:%dpx;text-align:right;' % (x - w, w)
        else:
            css += 'left:%dpx;' % x + ('width:%dpx;white-space:normal;' % w if w else '')
        self.parts.append('<div id="@@-%s" class="tx%s%s" style="%s%s">%s</div>'
                          % (sid, ' code' if code else '', ' ' + cls if cls else '', css, style, s if '<span' in s else esc(s)))
        if t is not None:
            lt = self.L(t)
            if anim == 'write':
                dd = d or min(1.1, max(0.28, 0.035 * len(s)))
                self.jsl.append("WRITE('#@@-%s',%.3f,%.3f);" % (sid, lt, dd))
            elif anim == 'pop':
                self.jsl.append("POPIN('#@@-%s',%.3f);" % (sid, lt))
            elif anim == 'rise':
                self.jsl.append("RISE('#@@-%s',%.3f);" % (sid, lt))
            else:
                self.jsl.append("FADE('#@@-%s',%.3f,%.3f);" % (sid, lt, d or 0.3))
        return sid

    def box(self, sid, x, y, w, h, label, size=44, t=None, stroke=INK, fill=None, fs='hachure', anim='draw',
            d=0.45, color=INK, sw=2.6, shape='rectangle', code=False, rough_=1.1, lstyle=''):
        """Shape with a centred label. anim: draw (outline draws, label writes) | pop (whole box pops)."""
        if shape == 'ellipse':
            inner = self.g('ellipse', [w / 2, h / 2, w - 8, h - 8], stroke=stroke, fill=fill, fs=fs, sw=sw, rough_=rough_)
        else:
            inner = self.g('rectangle', [5, 5, w - 10, h - 10], stroke=stroke, fill=fill, fs=fs, sw=sw, rough_=rough_)
        self.parts.append(
            '<div id="@@-%s" class="bx" style="left:%dpx;top:%dpx;width:%dpx;height:%dpx;font-size:%dpx;color:%s">'
            '<svg class="sv" style="width:%dpx;height:%dpx" viewBox="0 0 %d %d">%s</svg>'
            '<div class="lb%s" id="@@-%s-l" style="%s">%s</div></div>'
            % (sid, x, y, w, h, size, color, w, h, w, h, inner, ' code' if code else '', sid,
               ("font-family:'Shanns',monospace;" if code else '') + lstyle, label if label.startswith('<') else esc(label)))
        if t is not None:
            lt = self.L(t)
            if anim == 'pop':
                self.jsl.append("POPIN('#@@-%s',%.3f);" % (sid, lt))
            else:
                self.jsl.append("SKD('#@@-%s',%.3f,%.3f);WRITE('#@@-%s-l',%.3f,%.3f);"
                                % (sid, lt, d, sid, lt + d * 0.55, min(0.8, max(0.25, 0.03 * len(label)))))
        return sid

    # -- media
    def img(self, sid, src, x, y, w, h, t=None, url=None, frame=True, d=0.5, img_css='', bar=52):
        """Screenshot in a sketchy browser window. The image sits at (x, y + bar) w x (h - bar)."""
        if frame:
            fr = self.g('rectangle', [x - 6, y - 6, w + 12, h + 12], stroke=INK, sw=3, rough_=0.9)
            fr += self.g('line', [x - 4, y + bar, x + w + 4, y + bar], stroke=INK, sw=2.4, rough_=0.8)
            for i, col in enumerate(('#ff8787', '#ffd43b', '#69db7c')):
                fr += self.g('ellipse', [x + 30 + i * 30, y + bar / 2, 17, 17], stroke=INK, sw=1.8, fill=col, fs='solid',
                             rough_=0.6)
            if url:
                fr += self.g('rectangle', [x + 140, y + 11, min(w - 200, 640), bar - 22], stroke=LGREY, sw=2, rough_=0.7)
            self.parts.append('<svg id="@@-%s-fr" class="sv" viewBox="0 0 1920 1080">%s</svg>' % (sid, fr))
            if url:
                self.parts.append('<div id="@@-%s-url" class="tx" style="left:%dpx;top:%dpx;font-size:28px;color:%s">%s</div>'
                                  % (sid, x + 160, y + bar / 2 - 15, GREY, esc(url)))
        iy, ih = (y + bar, h - bar) if frame else (y, h)
        self.parts.append('<div id="@@-%s" class="im" style="left:%dpx;top:%dpx;width:%dpx;height:%dpx">'
                          '<img id="@@-%s-i" src="%s" alt="" style="%s"/></div>' % (sid, x, iy, w, ih, sid, src, img_css))
        if t is not None:
            lt = self.L(t)
            if frame:
                self.jsl.append("SKD('#@@-%s-fr',%.3f,%.3f);" % (sid, lt, d))
                if url:
                    self.jsl.append("FADE('#@@-%s-url',%.3f);" % (sid, lt + d * 0.6))
            self.jsl.append("tl.fromTo('#@@-%s',{opacity:0,clipPath:'inset(0 0 100%% 0)'},{opacity:1,clipPath:'inset(0 0 0%% 0)',duration:0.55,ease:'power2.out'},%.3f);"
                            % (sid, lt + (d * 0.5 if frame else 0)))
        return sid

    def shot(self, sid, src, x, y, w=None, h=None, t=None, url=None, frame=True, d=0.5, bar=52):
        """Screenshot in a browser frame, sized from the PNG itself: give w OR h (the frame's outer size, bar
        included) and the other side follows, so the image is never cropped by object-fit. Returns a Shot:
        shot.r('text') -> (x, y, w, h) canvas box of a text measured by shoot.py (<src minus .png>.json),
        shot.pt(px, py) -> canvas point of an image pixel. Use them for rings / marks / arrows:
            s = k.shot('sh', 'assets/img/side.png', 160, 100, w=900, url='site.com')
            k.ring('r1', *s.r('Possible scam', pad=10), t=...)"""
        iw, ih = png_size(root_path(src))
        top = bar if frame else 0
        if w is None and h is None:
            raise ValueError('k.shot needs w or h')
        if w is None:
            w = round((h - top) * iw / ih)
        h = round(w * ih / iw) + top
        self.img(sid, src, x, y, w, h, t=t, url=url, frame=frame, d=d, bar=bar)
        side = root_path(src)[:-4] + '.json'
        boxes = json.load(open(side))['boxes'] if os.path.exists(side) else {}
        return Shot(src, x, y + top, w / iw, boxes, w, h - top)

    def logo(self, sid, name, x, y, size, t=None, color=None, anim='pop'):
        """Brand logo. `name` = a simple-icons slug (claude, upwork, cloudflare, notion, github, ...; see
        https://simpleicons.org) or a file assets/logos/<name>.svg you added yourself. Brand colour by default;
        near-white brand colours fall back to ink so they show on paper."""
        p = root_path('assets/logos/%s.svg' % name)
        if os.path.exists(p):
            svg, col = open(p).read(), color or LOGO_COLORS.get(name, INK)
        else:
            ic = rough('icon', [name])
            svg, col = ic['svg'], color or LOGO_COLORS.get(name) or '#' + ic['hex']
            r, g, b = (int(col[i:i + 2], 16) for i in (1, 3, 5))
            if 0.2126 * r + 0.7152 * g + 0.0722 * b > 215:
                col = INK
        svg = svg.replace('<svg ', '<svg fill="%s" ' % col, 1)
        self.parts.append('<div id="@@-%s" class="lg" style="left:%dpx;top:%dpx;width:%dpx;height:%dpx">%s</div>'
                          % (sid, x, y, size, size, svg))
        if t is not None:
            self.jsl.append(("POPIN('#@@-%s',%.3f);" if anim == 'pop' else "FADE('#@@-%s',%.3f);") % (sid, self.L(t)))
        return sid

    def raw(self, h):
        self.parts.append(h)

    def js(self, code):
        self.jsl.append(code)

    def css(self, code):
        self.cssl.append(code)

    # -- chapter tag in the corner of every B-roll scene of a point
    def _tag(self, n, label):
        self.parts.append('<div id="@@-tag" class="tx" style="left:96px;top:58px;font-size:34px;color:%s">'
                          '<span style="color:%s">#%d</span>  %s</div>' % (GREY, INK, n, esc(label)))

    # -- assemble
    def done(self):
        c = self.c
        js = []
        if self.prev_face:
            js.append("tl.fromTo('#@@-paper',{x:2060,rotation:2.2},{x:0,rotation:0,duration:0.6,ease:'expo.out'},0);")
            sfx('whoosh', c.t0 + 0.06, 0.32)
        else:
            js.append("tl.fromTo('#@@-cam',{x:460,opacity:0,filter:'blur(10px)'},{x:0,opacity:1,filter:'blur(0px)',duration:0.55,ease:'expo.out'},0);"
                      "tl.fromTo('#@@-grid',{x:460},{x:0,duration:0.55,ease:'expo.out'},0);")
            Sk._whoosh += 1
            sfx(('whoosh-short', 'whoosh-high')[Sk._whoosh % 2], c.t0 + 0.02, 0.2)
        js += self.jsl
        end = c.dur
        if self.next_face:
            js.append("tl.to('#@@-paper',{x:-2060,rotation:-1.8,duration:0.42,ease:'power3.in'},%.3f);" % (end - 0.45))
            sfx('whoosh-low', c.t0 + end - 0.08, 0.3)
        else:
            js.append("tl.to('#@@-cam',{x:-460,opacity:0,filter:'blur(10px)',duration:0.3,ease:'power3.in'},%.3f);"
                      "tl.to('#@@-grid',{x:-460,duration:0.3,ease:'power3.in'},%.3f);" % (end - 0.33, end - 0.33))
        body = ('<div id="@@-paper" class="paper"><div id="@@-grid" class="grid"></div>'
                '<div id="@@-cam" class="cam" data-layout-allow-overlap>\n%s\n</div></div>' % '\n'.join(self.parts))
        return body, '\n'.join(self.cssl), '\n'.join(js)


LOGO_COLORS = dict(claude='#D97757', upwork='#14a800', cloudflare='#F38020', googlechrome='#4285F4', github='#181717',
                   reddit='#FF4500', youtube='#FF0000', anthropic='#191919')

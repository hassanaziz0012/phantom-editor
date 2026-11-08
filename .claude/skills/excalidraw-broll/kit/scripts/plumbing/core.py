"""Style-free HyperFrames build plumbing.

  setup(...)           project paths, size, audio, and the video's own CSS / caption style
  S(t, fn, **kw)       register a scene starting at t (s); it lasts until the next scene starts
  sfx(name, hit, vol)  place an SFX so its measured peak lands on `hit`
  cap(text, t, dur)    add a caption ("*word*" gets class acc)
  build()              write compositions/<id>.html per scene, compositions/captions.html, index.html

A scene function is fn(c, **kw) -> (body_html, css, js). Inside all three, @@ is replaced by the
scene id: prefix every selector/id with it (a bare class selector hits other scenes).
`c` is a Ctx: c.p = scene id, c.t0/c.dur = start/length, c.L(t_abs) = scene-local time,
c.end(back) = local time `back` s before the scene ends. JS runs with `tl` (the scene timeline)
and HELPERS_JS in scope.
"""
import html as _html, json, math, os, random, re, subprocess

from .anchors import T, word_times, load as load_transcript  # noqa: F401  (re-exported for scenes)

CFG = dict(
    root='.', total=None, fps=30, width=1920, height=1080,
    transcript='research/words.txt',
    voice='assets/audio/voice.m4a', voice_volume=1.0,
    bgm=None, bgm_volume=0.13, bgm_fade_in=1.5,
    peaks='research/sfx-peaks.json',
    css='',           # video's shared CSS, injected into every scene
    js='',            # video's extra shared JS helpers
    head='',          # extra tags for index.html <head> (fonts, ...)
    bg='#000',        # index.html page background (seen only where no scene paints)
    caption_css='',   # video's caption style (.capg = clip wrapper, .capin = box, .cw = word, .acc)
    caption_markup=None,  # fn(text) -> inner html; default words(text, 'cw')
    caption_anim=None,    # fn(sel, t, dur) -> js; default plain opacity in/out
    sfx_nudge=None,
    underlay='',      # raw html placed in the root before the scene hosts (e.g. the face <video>)
)

SCENES, SFX, CAPTIONS = [], [], []


def setup(**kw):
    unknown = set(kw) - set(CFG)
    if unknown:
        raise TypeError('unknown setup keys: %s' % sorted(unknown))
    CFG.update(kw)
    CFG['root'] = os.path.abspath(CFG['root'])
    if CFG['transcript'] and os.path.exists(path(CFG['transcript'])):
        load_transcript(path(CFG['transcript']))
    if CFG['total'] is None:
        CFG['total'] = round(probe_length(path(CFG['voice'])), 3)


def path(rel):
    return os.path.join(CFG['root'], rel)


def probe_length(p):
    out = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', p],
                         capture_output=True, text=True, check=True).stdout
    return float(out.strip())


# ---------------------------------------------------------------- registry
def S(t, fn, **kw):
    SCENES.append(dict(t=round(t, 3), fn=fn, kw=kw))


def sfx(name, hit, vol=0.4):
    SFX.append((name, hit, vol))


def cap(text, t, dur=None):
    if dur is None:
        dur = max(1.6, 0.6 + 0.32 * len(text.replace('*', '').split()))
    CAPTIONS.append((text, t, dur))


class Ctx:
    def __init__(self, sid, t0, dur):
        self.p, self.t0, self.dur = sid, t0, dur

    def L(self, t_abs):
        return round(max(0.0, t_abs - self.t0), 3)

    def end(self, back=0.0):
        return round(self.dur - back, 3)


# ---------------------------------------------------------------- markup helpers
def esc(t):
    return _html.escape(t, quote=False)


def words(text, cls='w'):
    """'a *two words* here' -> one <span class=cls> per word; words inside *...* also get class acc."""
    out, on = [], False
    for w in text.split():
        start = w.startswith('*')
        stop = w.endswith('*') and (len(w) > 1 or not start)
        on = on or start
        out.append('<span class="%s%s">%s</span>' % (cls, ' acc' if on else '', esc(w.strip('*'))))
        on = on and not stop
    return ' '.join(out)


def inline_svg(rel, cls=''):
    """Inline an SVG file (relative to root) with width/height removed so CSS sizes it."""
    s = re.sub(r'<!--.*?-->|<\?xml.*?\?>', '', open(path(rel)).read(), flags=re.S)
    s = re.sub(r'(<svg\b[^>]*?)\s(width|height)="[^"]*"', r'\1', s, count=1)
    s = re.sub(r'(<svg\b[^>]*?)\s(width|height)="[^"]*"', r'\1', s, count=1)
    return s.replace('<svg', '<svg class="%s"' % cls, 1) if cls else s


def seeded(seed):
    return random.Random(seed)


# ---------------------------------------------------------------- screenshot helpers
def load_boxes(rel='research/boxes.json'):
    """boxes.json from boxes.py: {img: {"h_css": page height, "boxes": {text: [x, y, w, h] as page fractions}}}"""
    return json.load(open(path(rel)))


def box(boxes, img, key):
    """Box for `key` on screenshot `img`; key may be a prefix of the measured text."""
    b = boxes[img]['boxes']
    if b.get(key):
        return b[key]
    for k, v in b.items():
        if k.startswith(key) and v:
            return v
    raise KeyError((img, key))


def box_px(b, img_w, img_h):
    """Fractional box -> (x, y, w, h) in pixels of the image as displayed."""
    return b[0] * img_w, b[1] * img_h, b[2] * img_w, b[3] * img_h


def pan_js(sel, img_w, img_h, view_w, view_h, focus, z0=1.0, z1=1.18, start_focus=None, t=0.0, d=1.0,
           ease='power2.inOut'):
    """Ken Burns move of an image element (transform-origin 0 0) inside a view_w x view_h window.
    focus / start_focus: [fx, fy] page fractions or a box [x, y, w, h]. The view never leaves the image."""
    def cam(f, z):
        fx, fy = ((f[0] + f[2] / 2), (f[1] + f[3] / 2)) if len(f) == 4 else f
        tx = min(0, max(view_w - img_w * z, view_w / 2 - fx * img_w * z))
        ty = min(0, max(view_h - img_h * z, view_h / 2 - fy * img_h * z))
        return round(tx, 1), round(ty, 1)
    (x0, y0), (x1, y1) = cam(start_focus or focus, z0), cam(focus, z1)
    return ("tl.fromTo('%s',{x:%s,y:%s,scale:%s},{x:%s,y:%s,scale:%s,duration:%.3f,ease:'%s'},%.3f);"
            % (sel, x0, y0, z0, x1, y1, z1, d, ease, t))


# ---------------------------------------------------------------- shared js (motion only, no look)
HELPERS_JS = r"""
const Q=s=>document.querySelectorAll(s);
const IN=(s,t,o={})=>tl.fromTo(s,{opacity:0,filter:`blur(${o.b??8}px)`,x:o.x??0,y:o.y??0,scale:o.s??1},{opacity:1,filter:'blur(0px)',x:0,y:0,scale:1,duration:o.d??0.55,ease:o.e??'expo.out',stagger:o.st??0},t);
const OUT=(s,t,o={})=>tl.to(s,{opacity:0,filter:`blur(${o.b??6}px)`,duration:o.d??0.22,ease:'power3.in',stagger:o.st??0},t);
const SLAM=(s,t,o={})=>tl.fromTo(s,{opacity:0,scale:o.s??1.4,rotation:o.r??0},{opacity:1,scale:1,rotation:o.r??0,duration:o.d??0.24,ease:'power4.out'},t);
const POP=(s,t,st=0,o={})=>tl.fromTo(s,{opacity:0,scale:o.s??0.55},{opacity:1,scale:1,duration:o.d??0.5,ease:'back.out(1.6)',stagger:st},t);
const WIPE=(s,t,d=0.5,dir='l')=>tl.fromTo(s,{clipPath:dir=='l'?'inset(0 100% 0 0)':'inset(100% 0 0 0)'},{clipPath:'inset(0 0% 0 0)',duration:d,ease:'power3.inOut'},t);
const DRAW=(s,t,d=0.4,e='power2.inOut')=>{Q(s).forEach(el=>{const L=el.getTotalLength?el.getTotalLength():2000;el.style.strokeDasharray=(L+40)+' '+(L+40);tl.fromTo(el,{strokeDashoffset:L+60},{strokeDashoffset:0,duration:d,ease:e},t)})};
const COUNT=(s,t,a,b,d,fmt,e='power2.out')=>{const o={v:a};const els=Q(s);const set=()=>els.forEach(el=>el.textContent=fmt(o.v));set();tl.to(o,{v:b,duration:d,ease:e,onUpdate:set},t)};
const SET=(s,t,v)=>tl.set(s,v,t);
const fmtInt=v=>Math.round(v).toLocaleString('en-US');
"""

BASE_CSS = '#root{position:absolute;inset:0;overflow:hidden}\n'


# ---------------------------------------------------------------- writers
def _comp(cid, css, body, js, root_attrs=''):
    return ('<template>\n<style>\n%s</style>\n'
            '<div id="root" data-composition-id="%s" data-width="%d" data-height="%d"%s>\n%s\n</div>\n'
            '<script>\n(()=>{\nconst tl=gsap.timeline({paused:true});\n%s\nwindow.__timelines["%s"]=tl;\n})();\n</script>\n'
            '</template>\n') % (css, cid, CFG['width'], CFG['height'], root_attrs, body, js, cid)


def _write(rel, text):
    with open(path(rel), 'w') as f:
        f.write(text)


def _lane(lanes, start, end):
    """First lane free at `start`; opens a new lane instead of overlapping."""
    for i, busy_until in enumerate(lanes):
        if busy_until <= start + 1e-6:
            lanes[i] = end
            return i
    lanes.append(end)
    return len(lanes) - 1


def write_scene(sid, body, css, js):
    _write('compositions/%s.html' % sid, _comp(
        sid, BASE_CSS + CFG['css'] + '\n' + css.replace('@@', sid), body.replace('@@', sid),
        HELPERS_JS + CFG['js'] + '\n' + js.replace('@@', sid)))


def _default_caption_anim(sel, t, dur):
    return ("tl.fromTo('%s .cw',{opacity:0},{opacity:1,duration:0.16,stagger:0.06},%.3f);\n"
            "tl.to('%s',{opacity:0,duration:0.18},%.3f);" % (sel, t, sel, t + dur - 0.2))


def write_captions():
    markup = CFG['caption_markup'] or (lambda text: words(text, 'cw'))
    anim = CFG['caption_anim'] or _default_caption_anim
    groups, js, lanes = [], [], []
    for i, (text, t, dur) in enumerate(sorted(CAPTIONS, key=lambda c: c[1])):
        cid = 'cap%02d' % i
        dur = min(dur, CFG['total'] - t)  # never run past the film
        groups.append('<div id="%s" class="clip capg" data-start="%.3f" data-duration="%.3f" data-track-index="%d">'
                      '<div class="capin" id="%s-in">%s</div></div>'
                      % (cid, t, dur, 1 + _lane(lanes, t, t + dur), cid, markup(text)))
        js.append(anim('#%s-in' % cid, t, dur))
    js.append('tl.set({}, {}, %.3f);' % CFG['total'])  # timeline spans the whole film
    _write('compositions/captions.html', _comp(
        'captions', '#root{position:absolute;inset:0;pointer-events:none}\n' + CFG['caption_css'],
        '\n'.join(groups), '\n'.join(js), ' data-layout-allow-caption-zone'))


def write_index(hosts, audio):
    _write('index.html', """<!doctype html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=%(w)d, height=%(h)d" />
<script src="https://cdn.jsdelivr.net/npm/gsap@3.15.0/dist/gsap.min.js"></script>
%(head)s
<style>
*{margin:0;padding:0;box-sizing:border-box}
html,body{margin:0;width:%(w)dpx;height:%(h)dpx;overflow:hidden;background:%(bg)s}
#root{width:100%%;height:100%%}
</style>
</head>
<body>
<div id="root" data-composition-id="main" data-start="0" data-duration="%(total).3f" data-width="%(w)d" data-height="%(h)d">
%(hosts)s
%(audio)s
</div>
<script>
window.__timelines["main"] = gsap.timeline({ paused: true });
</script>
</body>
</html>
""" % dict(w=CFG['width'], h=CFG['height'], head=CFG['head'], bg=CFG['bg'], total=CFG['total'],
           hosts='\n'.join(hosts), audio='\n'.join(audio)))


def _audio_clips():
    total, fps = CFG['total'], CFG['fps']
    out = ['<audio id="voice" class="clip" src="%s" data-start="0" data-duration="%.3f" data-track-index="10" data-volume="%.2f"></audio>'
           % (CFG['voice'], total, CFG['voice_volume'])]
    if CFG['bgm']:
        out.append('<audio id="bgm" class="clip" src="%s" data-start="0" data-duration="%.3f" data-track-index="11" '
                   'data-volume="%.2f" data-fade-in="%.2f"></audio>' % (CFG['bgm'], total, CFG['bgm_volume'], CFG['bgm_fade_in']))
    if not SFX:
        return out
    peaks = json.load(open(path(CFG['peaks'])))
    nudge = 1.0 / fps if CFG['sfx_nudge'] is None else CFG['sfx_nudge']
    lanes = []
    for k, (name, hit, vol) in enumerate(sorted(SFX, key=lambda x: x[1])):
        p = peaks[name]
        start = max(0.0, hit - p['peak'] + nudge)
        slot = min(math.floor(p['duration'] * fps) / fps, total - start)  # real file length, whole frames
        if slot <= 0:
            continue
        out.append('<audio id="sfx%03d" class="clip" src="%s" data-start="%.3f" data-duration="%.3f" data-track-index="%d" data-volume="%.2f"></audio>'
                   % (k, p['file'], start, slot, 12 + _lane(lanes, start, start + slot), vol))
    return out


def build(only=None):
    """Write every scene (or only the ids in `only`), captions and index.html."""
    if CFG['total'] is None:
        raise RuntimeError('call setup(...) before build()')
    os.makedirs(path('compositions'), exist_ok=True)
    SCENES.sort(key=lambda s: s['t'])
    hosts = []
    for i, sc in enumerate(SCENES):
        sid = 's%02d' % (i + 1)
        t0 = sc['t']
        dur = round((SCENES[i + 1]['t'] if i + 1 < len(SCENES) else CFG['total']) - t0, 3)
        if dur <= 0:
            raise ValueError('scene %s (%s) at %.3f has no length' % (sid, sc['fn'].__name__, t0))
        if sc['fn'] is None:          # a gap: nothing hosted, the underlay shows through
            continue
        body, css, js = sc['fn'](Ctx(sid, t0, dur), **sc['kw'])
        if only is None or sid in only:
            write_scene(sid, body, css, js)
        hosts.append('<div id="%s" data-composition-id="%s" data-composition-src="compositions/%s.html" data-start="%.3f" '
                     'data-duration="%.3f" data-track-index="1" data-track-kind="graphics" data-width="%d" data-height="%d" '
                     'style="z-index:1" data-label="%s"></div>'
                     % (sid, sid, sid, t0, dur, CFG['width'], CFG['height'], sc['fn'].__name__))
    if CAPTIONS:
      hosts.append('<div id="captions" data-composition-id="captions" data-composition-src="compositions/captions.html" '
                 'data-start="0" data-duration="%.3f" data-track-index="2" data-track-kind="captions" data-width="%d" '
                 'data-height="%d" style="z-index:5"></div>' % (CFG['total'], CFG['width'], CFG['height']))
    write_index([CFG['underlay']] + hosts, _audio_clips())
    if CAPTIONS:
        write_captions()
    _write('scene-list.txt', ''.join('s%02d %7.2f %s\n' % (i + 1, sc['t'], sc['fn'].__name__ if sc['fn'] else 'FACE') for i, sc in enumerate(SCENES)))
    print('scenes', len(SCENES), 'sfx', len(SFX), 'captions', len(CAPTIONS), 'total %.2fs' % CFG['total'])

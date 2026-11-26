# Excalidraw B-roll: writing scenes.py

Read once at step 4 of SKILL.md. Exact signatures: `python3 scripts/tools.py api` (or `api NAME ...`).

## Screenshots (using them in scenes)

The `broll-assets` agent fetches them (shoot.py, Claude in Chrome, fileshot, record.py; the how-to is in
`.claude/agents/broll-assets.md`). Public-page shots come with `<name>.json` measured boxes for every
text you asked it to measure; Chrome shots don't.

- Show a screenshot with `s = k.shot(sid, 'assets/img/x.png', x, y, w=...)` (or `h=`): it draws the
  sketchy browser frame, sizes the other side from the PNG so nothing is cropped, and returns a Shot.
  Put rings, rects and marks on measured boxes: `k.ring('r', *s.r('Possible scam'), t=...)`; `s.pt(px, py)`
  maps any image pixel. Never place them by eye (that cost a fix round in the Reddit-jobs video).
  `k.img(...)` is the low-level version (you size it; object-fit crops a wrong aspect). For images with no
  measured json (Chrome shots), `s.r_img(px, py, pw, ph, pad=)` maps an image-pixel box to a canvas box.
- **Screen recordings** (scroll through a live site, sliders, scroll animations): ask the agent for one with
  the record.py steps (wait / scroll to selector / scrollby / drag across a selector (fractions 0.01-0.99) /
  click / hover), timed to the transcript; it reports when each step starts. Play it with
  `k.video(sid, src, x, y, w, t=, dur=, url=)` inside the browser frame (w=1320 at (110, 90) fills the
  space left of the face square).
- Long tweets: the embed cuts them at "Show more"; if the agent flags one, crop the scene to what's shown or
  ask the user.

## API cheat sheet (`scenes.py`)

Canvas is 1920×1080. Keep content inside x 96-1824 and y 54-1026, and out of the face square's corner
(x > 1480 and y > 640) unless the scene has `face=False`. The corner tag sits at (96, 58), titles
at about (160, 130) in 64-72 px, body text in 44-60 px, hero numbers in 150-400 px, and nothing smaller
than 28 px. Times are **absolute seconds**: `T('phrase')` is the first match in the whole video, and
`A(c, 'phrase')` is the first match from this scene's start, and `at(c, 'phrase', off=0)` is the same but
never before 0.5 s into the scene (use `at` for element times inside scenes). `t=None` means the
element is already there when the scene opens. Every `sid` is unique within its scene.

```python
k = K(c, kw, 'scene_name', n)          # n = list item for the corner tag, or omit
# shapes (stroke=, fill=, fs='hachure'|'solid'|'cross-hatch'|'zigzag', sw=, rough_=, dash=[14,12])
k.rect(sid, x, y, w, h, t=, d=0.5, ...)      k.ellipse(sid, cx, cy, w, h, t=)
k.line(sid, x1, y1, x2, y2, t=)              k.poly(sid, pts, t=, closed=False)   k.curve(sid, pts, t=)
k.arrow(sid, [(x,y),(x,y),...], t=, head=24, both=False, stroke=, sw=)   # >2 points = curved
k.check(sid, x, y, s=1, t=, color=GREEN)     k.cross(sid, x, y, s=1, t=)     # with click/pop SFX
k.strike(sid, x, y, w, t=)  k.underline(sid, x, y, w, t=, color=)  k.ring(sid, x, y, w, h, t=)   # ring = hand circle
k.mark(sid, x, y, w, h, t=, color=YEL_BG)    # highlighter wiped in behind text
# text: anim 'write' (hand-written wipe) | 'pop' | 'rise' | 'fade'; align 'l'|'c'|'r' (x is the anchor)
k.text(sid, x, y, 'text\nlines', size, t=, color=, w=None, align='l', anim='write', d=None, code=False)
k.box(sid, x, y, w, h, 'label', size, t=, stroke=, fill=, anim='draw'|'pop', shape='rectangle'|'ellipse', code=)
s = k.shot(sid, 'assets/img/x.png', x, y, w=None, h=None, t=None, url=None)  # s.r('text') -> (x,y,w,h), s.pt(px,py)
k.img(sid, 'assets/img/x.png', x, y, w, h, t=None, url='site.com/path', bar=52)
k.video(sid, 'assets/video/rec.mp4', x, y, w, t=, dur=, url=)   # screen recording in a browser frame
k.logo(sid, 'simpleicons-slug', x, y, size, t=, color=None)
k.raw(html)  k.js(code)  k.L(t_abs) -> scene time   k.g(op, args, **style) -> svg group for k.svg(sid, inner, t)
return k.done()
```

- `D` (doodles): every one is `D.name(k, sid, ...)`; `t` is absolute (None = already there). The list with
  exact signatures and anchor points: `python3 scripts/tools.py api doodles` (part of `tools.py api`).
- `blocks`: `LIST.update(title=, recap_title=, items=[...])`, `s_listcard` (`n=, sub=, item_at=, sub_at=`,
  `head_at=` on the first card, or `recap=True`; `check_x=860` with the face square), `tag(n)`. A board
  entry: `(t, blocks.s_listcard, {'n': 3, 'check_x': 860, 'item_at': T('phrase')})`. `item_at` must land at least
  1 s before the card ends, or the label is still being written when the card exits.
- Colours: `INK GREY LGREY FAINT`, then `RED/RED_BG GREEN/GREEN_BG BLUE/BLUE_BG ORANGE/YEL_BG VIOLET/VIO_BG
  CLAUDE/CLAUDE_BG`. Excalidraw's palette; one accent per idea.
- SFX: `sfx(name, hit_abs, vol)`. Volume 0.15-0.3, and the peak lands on `hit`. Names: whoosh,
  whoosh-short, whoosh-high, whoosh-low, pop, pop-low, click, click-soft, key-press, typing, chime, sparkle,
  impact-bass-1, riser. Transitions add their own whooshes. Add effects only for visible hits: a number
  landing, a stamp, typing. `check()`, `cross()` and `bubble()` already add their own sound.
- JS helpers in `k.js(...)`: `COUNT(sel,t,a,b,d,fmtInt)`, `POPIN(sel,t)`, `RISE`, `FADE`, `WRITE`,
  `DRAW(sel+' .o path',t,d)`. Use `tl.fromTo(...)` for anything custom, with ids written as `#@@-sid`.

## Custom JS / HTML (k.js, k.raw): the render rules you must keep

The `k.*` helpers already follow these. They only matter when you write your own markup or tweens.

- Use `tl.fromTo(...)` with explicit start and end states, never `tl.from`. Every scene is re-seeked, and
  `from` drifts.
- Nothing may depend on wall-clock time: no `Date.now()`, no unseeded `Math.random()` (use `random.Random(seed)`
  in Python at build time), no timers, no `repeat: -1`, no `requestAnimationFrame`, no network fetches. Every
  frame must be a function of timeline time.
- Don't put a CSS `transform` on an element that GSAP also transforms; put the start state inside `fromTo`.
  Animate transforms, `opacity` and `clip-path`, never `left/top/width/height`. Never tween
  `display`/`visibility`.
- Prefix every id and selector with `@@-` (it becomes the scene id). A bare class selector hits other
  scenes.
- Asset paths are root-relative (`assets/img/x.png`), even inside a scene.
- `%` inside %-formatted Python strings must be written `%%`.

## Motion craft (keep it feeling hand-made, not generated)

- Arrivals use long-tailed eases (`expo.out`, `back.out(1.7)` for pops). Exits are 40-50% shorter.
  Elements in a group overlap: the next starts when the previous is 30-50% done.
- Once a title or label has landed, it stays still. No floating, pulsing or breathing loops, and no slow
  creeping zoom. Keep the motion going elsewhere instead: the next stroke drawing, a counter, a scrolling
  feed, a needle swinging.
- Vary how things enter within a video: hand-written wipe, pop, rise, draw-on, a counter, a stamp. Don't
  use one entrance for everything.
- One accent colour per idea, plus one highlighter mark per scene at most.

## Gotchas (already solved; don't re-learn)

- A text element and the span inside it must have different ids, or `COUNT` overwrites the whole line.
- Rotate SVG pieces with `svgOrigin` on an inner `<g>`, never on the `<svg>`.
- `est_w(text, size) ≈ len*size*0.5` is only an estimate. Put check columns, rings and underlines at
  fixed x values. layout.py prints each text's real box (x,y WxH) when it reports it, so use those numbers
  instead of guessing; rings and underlines on a word still need a look on the snapshots.
- A long label inside `k.box` wraps; make the box wider or the font smaller. Text placed at the right edge
  with `align='l'` overflows, so keep x + width ≤ 1824.
- Phrases repeat across a video, which is why scenes use `A(c, ...)`. `T('anyway')` once matched an earlier
  "use anyway". In `board()`, pass `T('phrase', after=seconds)` when a phrase is ambiguous.
- Transcript word times are sometimes slightly out of order. Anchor on the first word of a phrase.
- The B-roll root is transparent and the paper sheet covers the face. Never put a background on `#root`.
- simple-icons dropped some brands (`openai`). Fetch the old icon into `assets/logos/<name>.svg` from
  `https://cdn.jsdelivr.net/npm/simple-icons@9.21.0/icons/<name>.svg`; `k.logo` picks it up.
- `s_listcard` scales its circles and labels with the row step, so 10-13 item lists fit (13 tasks video).
  With the face square, pass `check_x=860` so the done-ticks clear the big number's ellipse.
- Excalifont digits sit high in their line box: a big number placed at the top of its circle by line-height
  looks pushed up (user flagged it in the 13-tasks video). `s_listcard` places it at `cy - size*0.5125`
  (glyph centre on the ellipse centre) and drops two-digit numbers to 330 px. Use the same offset for any
  big number you centre in a shape yourself.
- `k.video` draws its frame with `assets/img/blank.png` (setup.sh copies it from `kit/assets/img`). A clip
  shorter than its scene leaves an empty frame after it ends: put a still of its last frame under it with
  `k.img(..., x, y + 52, w, video_h, frame=False)` at the same `t`.
- Speech-to-text splits brand names into separate words ("open AI", "deep seek") and keeps symbols on numbers
  ("90%"). Before the first build, run `tools.py anchors` to catch these, not trial-and-error builds.

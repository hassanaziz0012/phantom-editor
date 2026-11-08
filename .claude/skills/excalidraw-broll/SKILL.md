---
name: excalidraw-broll
description: Use when the user asks to create Excalidraw-style (hand-drawn, whiteboard, sketchy) B-roll for a YT video project, or to turn a talking-head final.mp4 into a face/B-roll cut. Produces <project>/final-broll.mp4 where full-screen HyperFrames B-roll alternates with the user's face.
---

# Excalidraw B-roll for a talking-head video

The user records videos that are only their face talking to camera. This skill turns `final.mp4` into
`final-broll.mp4`: **60-70% of the runtime is full-screen, light, Excalidraw-style B-roll** made with
HyperFrames, and the rest is the user's face, full screen. The audio is the original voice plus quiet BGM
and SFX. There are no captions.

All the plumbing is already built and proven in `kit/`, so your job is the creative part: a storyboard and
scenes that are **unique to this video**. Reference build: "7 practical, high ROI tasks for idle agents"
(Nov 2026, the user loved it). It is in `reference/`.

`KIT=/home/hassan/Desktop/programming/phantom-editor/.claude/skills/excalidraw-broll/kit`

## Defaults (don't ask)

- Inputs: `<project>/final.mp4`, `trimmed.srt` (you read it), `trimmed-1word.srt` (the kit reads it).
  Projects live in `~/Videos/YT Projects/<name>/`. Match a loose name with `ls`.
- B-roll share is 60-70%. Use quiet BGM (`Hello World.mp3` from `~/Videos/Asset Library/BGM`, or pass
  another file name to setup.sh) and SFX. No captions. Use real screenshots and logos of the things the
  user names.
- Ask only when a required input is missing or the user's message conflicts with these defaults. Then work
  end-to-end without stopping. Don't upload, post or publish anything.

## Token budget (this is why the skill exists)

The first build cost about 300k tokens. These rules keep a new one well under that.

- **Don't** load the `hyperframes*` skills, their references, or the plumbing/sketch source. The API and
  render rules below are all you need (exception: *When something breaks*). Open `kit/scripts/sketch.py` or `doodles.py` only to check one signature with `grep -n`.
- **Don't** read `reference/idle-agents-scenes.py` top to bottom. `grep -n "def s_"` it, then read one or
  two scenes if you need an example of the pattern. Look at `reference/look-1.jpg` once to see the look.
- Read `trimmed.srt` once, plus `outline.md` if it exists. For exact timings use
  `python3 scripts/tools.py find "phrase"` or `tools.py words FROM TO`. Never dump all 1500 words.
- Write `scenes.py` in 2-3 big `Write` calls. Apply fix rounds as one Python string-replace script, not
  many small `Edit`s.
- Visual review: one `snap.sh` pass (about 4-5 contact sheets), one fix pass, then re-snap only the scenes
  you changed. Read contact sheets, never single frames.
- No test render: the render path is proven. Render once, in the background, then do one spot-check Read
  of `research/final-sheet.jpg`.
- Don't poll a background job. You are notified when it finishes.

## Steps

Keep `broll/HANDOFF.md` updated after each step (the user wants this so a run can resume after rate
limits).

1. **Setup**: run `bash $KIT/setup.sh "<project>"`. This takes about 2 min. It creates `<project>/broll/`
   (HyperFrames project, kit scripts, symlinked node tools, fonts, SFX + peaks), `voice.m4a`, `words.txt`
   and a looped BGM. It also starts re-encoding the face video in the background (about 4 min). Safe to
   re-run.
2. **Read and storyboard**: read `trimmed.srt`. Decide the face segments and the B-roll scenes
   (rules below). Each B-roll scene has a concept, an "information shape", its anchor words, and the real
   assets it needs.
3. **Assets**: get screenshots of the real things named (see *Screenshots*). Logos come straight from
   simple-icons slugs; nothing to download.
4. **Write `broll/scripts/scenes.py`** from the template that is already there: `board()` plus one function
   per scene. Then run `python3 scripts/build.py && python3 scripts/tools.py share`. share prints the
   B-roll % and flags scenes shorter than 2.3 s. Adjust the board until the split is 60-70%.
5. **Review**: run `bash $KIT/snap.sh "<project>/broll"`, then Read the printed contact sheets. Each frame
   is 0.6 s before its scene ends, so everything should be visible. Fix overlaps, overflow past the
   right edge, late elements, and wrong text. Then run `python3 scripts/tools.py check` (13 s). Fix
   errors and `line_unread` warnings (a line that isn't on screen long enough to read). Re-snap only the
   changed scenes: `bash $KIT/snap.sh <broll> snap2 "t1,t2,..."`.
6. **Render**: run `bash $KIT/render.sh "<project>/broll"` with `run_in_background`. It takes about
   20-25 min for 7 min of video. It waits for the face encode, renders, and copies the result to
   `<project>/final-broll.mp4`. It prints duration and loudness and writes `research/final-sheet.jpg`
   (24 frames). Read that sheet once. If something is wrong, fix it and re-render.
7. **Report**: give the path, the B-roll %, and anything the user should check: illustrative numbers,
   claims you hedged, screenshots of their private pages and what you cropped out.

## Storyboard rules

- **Face** (about 30-40%): the opening line (establish the person), personal stories ("like me", "as you
  can see"), jokes and swearing, rants and opinions, "I might get hate for this", the like/subscribe ask
  (always face), the outro. Face segments last at least about 2.5 s.
- **B-roll** (60-70%): anything that explains, lists, compares, shows a product, a process, a number, or
  a prompt. Consecutive B-roll scenes pan from one to the next, so long B-roll stretches are fine.
- One scene covers one idea, about 5-15 s and roughly 19 spoken words. Longer is fine if parts are
  revealed in stages (a pipeline that grows box by box over 17 s worked well).
- **Every element lands on the word that names it** (`A(c, 'word')`). The scene's exit animation starts
  0.45 s before its end, so anchor nothing in the last ~0.8 s. A line must stay on screen 0.5 s plus
  0.3 s per word.
- Listicles: use `blocks.s_listcard` at each "Number N" (the list fills in, the current item gets a
  highlight, and a big circled number), a corner tag on that item's scenes, and a recap at the end.
  Non-list videos skip blocks entirely and still need some running structure of their own.

## Make it unique (not templated)

The style stays fixed: Excalidraw light, Excalifont, rough strokes, hatched pastels, dotted grid, and the
paper/pan transitions. **The compositions must come from this video's content.**

- Pick the **shape of the information** in each line first, then draw it. Shapes to choose from: a number
  that lands, a myth struck through and the truth written over it, before vs after, many things funnelled
  into a few, one chosen out of many, steps in order (a growing pipeline), a loop, a versus split, a
  timeline or race, a real screenshot with the detail circled, a typed prompt, a quote, a checklist, a
  decision fork, a chart line rising. Similar lines get different pictures.
- No two consecutive scenes share a layout. Don't fall back to "title + three boxes".
- Use **the user's real artefacts** where they talk about them: their logged-in pages, their own files
  (`outline.md`, repos), their usage pages, the actual product sites. This is what made the reference
  video feel personal.
- Don't reuse the reference video's layouts. Use it for API usage and pacing only.
- Facts: only numbers the user said, or values read from a real screenshot. If you draw something like a
  bar comparison without data, label it "(illustrative)". Don't soften the user's claims, and don't
  invent product names. When unsure which tool they mean (e.g. "an SEO skill"), draw it generically.

## Screenshots

- Public page: `cd broll && python3 scripts/plumbing/shoot.py research/shots name=https://url ...`. This
  writes `name.png` at 2000x1250 plus a `-tall` version. Read it to check, crop with ffmpeg, and save the
  result as `assets/img/<name>.png`.
- Logged-in or Cloudflare-blocked pages (Upwork search, claude.ai usage, dashboards): use Claude in Chrome.
  Run `tabs_context_mcp` (createIfEmpty), then `navigate`, then `computer screenshot` with
  `save_to_disk: true`. It returns a path to a 1410x840 jpg. Crop out the avatar, the name, the chat or
  sidebar history and anything else private with `ffmpeg -vf crop=w:h:x:y`. Close the tab when you're
  done.
- Show a screenshot with `k.img(...)`, which draws a sketchy browser frame. Place rings or rects on the
  detail being discussed. Coordinates = image offset + crop position × scale, and the content starts at
  `y + bar` (bar is 52 px).

## API cheat sheet (`scenes.py`)

Canvas is 1920×1080. Keep content inside x 96-1824 and y 54-1026. The corner tag sits at (96, 58), titles
at about (160, 130) in 64-72 px, body text in 44-60 px, hero numbers in 150-400 px, and nothing smaller
than 28 px. Times are **absolute seconds**: `T('phrase')` is the first match in the whole video, and
`A(c, 'phrase')` is the first match from this scene's start (use it inside scenes). `t=None` means the
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
k.img(sid, 'assets/img/x.png', x, y, w, h, t=None, url='site.com/path', bar=52)
k.logo(sid, 'simpleicons-slug', x, y, size, t=, color=None)
k.raw(html)  k.js(code)  k.L(t_abs) -> scene time   k.g(op, args, **style) -> svg group for k.svg(sid, inner, t)
return k.done()
```

- `D` (doodles): `browser`, `code_file` (returns line positions you can ring), `file_icon`, `lightbulb`,
  `target`, `gauge`, `prompt` (typed into a Claude-orange box, with key SFX), `bubble`, `scroller`
  (an endless feed of cards), `counter` (counts a→b on the word), `sticky`, `bars`.
- `blocks`: `LIST.update(title=, recap_title=, items=[...])`, `s_listcard` (`n=, sub=, item_at=, sub_at=`,
  `head_at=` on the first card, or `recap=True`), `tag(n)`.
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

## When something breaks that this page doesn't cover

Only then load `/hyperframes-cli` (CLI and render errors) or `/hyperframes` (the composition contract), and
read only the section you need. Fix generic bugs in `$KIT` (not just in the project copy) and add a line to
Gotchas so the next run inherits the fix.

## Gotchas (already solved; don't re-learn)

- `final.mp4` has keyframes seconds apart, so setup.sh re-encodes it with `-g 30`. build.py only puts face
  clips where the face is visible. One long `<video>` hits PLAN_TOO_LARGE (a 2 GB cap that can't be raised
  from the CLI).
- `render --start/--end` still extracts all video frames, so a 20 s test takes about 4 min. That's why
  there's no test render.
- A text element and the span inside it must have different ids, or `COUNT` overwrites the whole line.
- Rotate SVG pieces with `svgOrigin` on an inner `<g>`, never on the `<svg>`.
- `est_w(text, size) ≈ len*size*0.5` is only an estimate. Put check columns, rings and underlines at
  fixed x values, and verify them on snapshots.
- A long label inside `k.box` wraps; make the box wider or the font smaller. Text placed at the right edge
  with `align='l'` overflows, so keep x + width ≤ 1824.
- Phrases repeat across a video, which is why scenes use `A(c, ...)`. `T('anyway')` once matched an earlier
  "use anyway". In `board()`, pass `T('phrase', after=seconds)` when a phrase is ambiguous.
- Transcript word times are sometimes slightly out of order. Anchor on the first word of a phrase.
- The B-roll root is transparent and the paper sheet covers the face. Never put a background on `#root`.

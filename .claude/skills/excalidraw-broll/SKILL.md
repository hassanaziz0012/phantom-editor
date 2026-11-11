---
name: excalidraw-broll
description: Use when the user asks to create Excalidraw-style (hand-drawn, whiteboard, sketchy) B-roll for a YT video project, or to turn a talking-head final.mp4 into a face/B-roll cut. Replaces <project>/final.mp4 with a cut where full-screen HyperFrames B-roll alternates with the user's face, and the face stays visible in a rounded square at the bottom right during B-roll.
---

# Excalidraw B-roll for a talking-head video

The user records videos that are only their face talking to camera. This skill turns the face-only `final.mp4` into
a B-roll cut: **60-70% of the runtime is full-screen, light, Excalidraw-style B-roll** made with
HyperFrames, and the rest is the user's face, full screen. **During B-roll the user's face stays on screen
in a rounded square at the bottom right** (picture-in-picture), so viewers see both. The audio is the
original voice plus quiet BGM and SFX. There are no captions.

**`final.mp4` is always the video that gets uploaded.** render.sh delivers the B-roll cut as `final.mp4`
and keeps the face-only original next to it as `final-face.mp4`. So if `final-face.mp4` exists, the
project already has a B-roll cut and the kit takes the face from `final-face.mp4`. (The pipeline deletes
`final-face.mp4` when it re-encodes `final.mp4`, for example after new cuts. `broll/` is then out of date:
delete it and start again.)

All the plumbing is already built and proven in `kit/`, so your job is the creative part: a storyboard and
scenes that are **unique to this video**. Reference build: "7 practical, high ROI tasks for idle agents"
(Nov 2026, the user loved it). It is in `reference/`.

`KIT=/home/hassan/Desktop/programming/phantom-editor/.claude/skills/excalidraw-broll/kit`

## Defaults (don't ask)

- Inputs: `<project>/final.mp4` (or `final-face.mp4`, see above), `trimmed.srt` (you read it), `trimmed-1word.srt` (the kit reads it).
  Projects live in `~/Videos/YT Projects/<name>/`. Match a loose name with `ls`.
- Every B-roll scene shows the face square unless its board entry says `{'face': False}` (see *The face
  square*). Keep the bottom-right corner of those scenes empty.
- B-roll share is 60-70%. Use quiet BGM (`Hello World.mp3` from `~/Videos/Asset Library/BGM`, or pass
  another file name to setup.sh) and SFX. No captions.
- **Real assets first.** Whenever a scene would be better showing a real website, app, screenshot, UI,
  file or video, get the real thing. A drawn illustration is the backup for when the real asset can't be
  had. See *Real assets*.
- Ask once, up front, about real assets (step 2b). Otherwise ask only when a required input is missing or
  the user's message conflicts with these defaults. Then work end-to-end without stopping. Don't upload,
  post or publish anything.

## Token budget (this is why the skill exists)

The first build cost about 300k tokens. These rules keep a new one well under that.

- **Don't** load the `hyperframes*` skills, their references, or the plumbing/sketch source. The API and
  render rules below are all you need (exception: *When something breaks*). Open `kit/scripts/sketch.py` or `doodles.py` only to check one signature with `grep -n`.
- **Don't** read `reference/idle-agents-scenes.py` top to bottom. `grep -n "def s_"` it, then read one or
  two scenes if you need an example of the pattern. Look at `reference/look-1.jpg` once to see the look.
- Read the transcript once with `python3 scripts/tools.py plan` (numbered sentences with start times),
  plus `outline.md` if it exists. For exact timings collect ALL of a storyboard's anchors in one call:
  `tools.py find "phrase 1" "phrase 2" ...` (one line per phrase; hyphenated words match their parts).
  `tools.py words FROM TO` only for a gap you can't phrase. Never dump all 1500 words.
- Write `scenes.py` in 2-3 big `Write` calls (Read the template once first: Write refuses to overwrite a
  file that hasn't been read). Apply fix rounds as one Python string-replace script, not
  many small `Edit`s.
- Visual review: `layout.py` first (text output, no images), then one `snap.sh` pass (about 4-5 contact
  sheets), one fix pass, then re-snap only the scenes you changed. Read contact sheets, never single frames.
- No test render for a normal video: the render path is proven. Render once, in the background, then do one
  spot-check Read of `research/final-sheet.jpg`. (Changing the kit's render path is different: test it with
  `render.sh <broll> --test FROM TO`, see step 6.)
- Don't poll a background job. You are notified when it finishes.

## Steps

Keep `broll/HANDOFF.md` updated after each step (the user wants this so a run can resume after rate
limits).

1. **Setup**: run `bash $KIT/setup.sh "<project>"`. This takes about 2 min. It creates `<project>/broll/`
   (HyperFrames project, kit scripts, symlinked node tools, fonts, SFX + peaks), `voice.m4a`, `words.txt`
   and a looped BGM. It also makes `face.mp4`: a few-second stream copy of `final.mp4`, or a background
   re-encode (about 4 min) if `final.mp4` predates the pipeline's 1 s keyframes, plus `pip.mp4` (the face
   square's small video, about 1 min). Safe to re-run; on a project set up before the face square existed,
   re-running it just adds `pip.mp4`.
2. **Read and storyboard**: read the transcript (`tools.py plan`). Decide the face segments and the B-roll scenes
   (rules below). Each B-roll scene has a concept, an "information shape", its anchor words, and the real
   assets it needs.
2b. **Ask about real assets (one message, before writing scenes)**: list every scene where a real asset
   would beat a drawing, in two groups: the ones you can fetch yourself (say from where) and the ones only
   the user can supply or point to (phone screenshots, private dashboards, a video link, a repo you can't
   find). Also list any name, domain or title you only know from the transcript and ask the user to
   confirm the spelling (speech-to-text gets these wrong: "watch.hustondev.me" was really
   watch.hassandev.me, "Excalibur" was Excalidraw). While you wait for the answer, run setup and fetch
   the assets in the first group.
3. **Assets**: collect the real assets (see *Real assets* and *Screenshots*). Logos come straight from
   simple-icons slugs; nothing to download.
4. **Write `broll/scripts/scenes.py`** from the template that is already there: `board()` plus one function
   per scene. Then run `python3 scripts/build.py && python3 scripts/tools.py share`. share prints the
   B-roll % and flags scenes shorter than 2.3 s. Adjust the board until the split is 60-70%.
5. **Review**: first the geometry, by script: `python3 scripts/plumbing/layout.py` (about 15 s; pass scene
   ids like `s05 s12` to re-check only those). It opens every B-roll scene in a browser and reads where
   each element really lands, across the whole scene. Fix everything it prints: `off_canvas`,
   `outside_safe`, `under_face` (something sits under the face square), `text_overlap`, `label_spills`,
   `crosses_box` (a text half in, half out of a box),
   `logo_overlap`, `small_text`, `never_shown` and `late` (anchored too close to the scene's end, or past
   it). `text_on_image` is fine when it is a callout you put there on purpose. Then the look, by eye: run
   `bash $KIT/snap.sh "<project>/broll"` and Read the printed contact sheets. Each frame is 0.6 s before
   its scene ends, so everything should be visible. Don't hunt for overflow or overlaps there, layout.py
   has done that. Look for what a script can't judge: wrong text, a weak or unclear picture, scenes that
   are mostly text (redraw them as pictures), a ring or arrow that points at the wrong thing. Then run
   `python3 scripts/tools.py check` (13 s). Fix errors and `line_unread` warnings (a line that isn't on
   screen long enough to read). After fixes, re-run layout.py on the changed scenes and re-snap only
   those: `bash $KIT/snap.sh <broll> snap2 "t1,t2,..."`.
6. **Render**: run `bash $KIT/render.sh "<project>/broll"` with `run_in_background`. Chrome only renders
   the B-roll windows (`render.html`, packed back to back, no face square, no audio); `assemble.py` then
   rebuilds the film with ffmpeg (face gaps from face.mp4, face square from pip.mp4, voice + BGM + SFX).
   It waits for the face encode, delivers the result as `<project>/final.mp4` (the face-only original
   becomes `final-face.mp4`), prints duration and loudness, writes `research/final-sheet.jpg` (24 frames)
   and prints a timing table per stage (wait-face, build, chrome, assemble, verify, sheet, deliver), also
   appended to `research/render-timings.log`. Read the sheet once. If something is wrong, fix and re-render.
   Measured on the Reddit-jobs video (7 min, 86% B-roll): chrome 522 s (370 s of packed B-roll, ~43 s of
   video per minute), assemble ~290 s (about 1.5x realtime), total ~14.5 min (the old all-in-Chrome render
   took 48 min). Time scales with B-roll seconds + film length. `--test FROM TO` renders just that span to `renders/test.mp4` (not delivered) with its own
   `research/test-sheet.jpg`. After changing the render path, render the full film with `--test 0 <end>` and
   run `tools.py compare renders/test.mp4 ../final.mp4` against a known-good cut (in sync: mean SSIM ~0.99+).
   A 20 s test once passed while the full film drifted 30 s.
   **Progress**: when the user asks how far along it is, run `python3 $KIT/progress.py "<project>/broll"`
   (one line: stage, % done, speed, minutes left; about 0.2 s, safe to repeat). render.sh also refreshes
   `research/progress.txt` every 30 s, so the user can `cat` it. Don't count frames or read logs by hand.
7. **Report**: give the path, the B-roll %, and anything the user should check: illustrative numbers,
   claims you hedged, which real assets you used, screenshots of their private pages and what you
   cropped out, and any scene that is still a drawing because the real asset couldn't be had.

## Storyboard rules

The hook treatment and the 60-70% B-roll share are defaults for when the user says nothing. If they ask
for something else for this video (a raw intro, 80% B-roll, much less B-roll), do that instead and scale
the other rules to fit. `tools.py share` only reports the split; it is not a target to hit then.

- **The hook (first 10 s) is the most heavily edited part of the video.** By default, don't leave it as
  raw face: the user wants the B-roll to grab attention from the first seconds. Open on the face for at most about 2 s
  (just enough to establish the person), then cut fast between B-roll and face: at least two B-roll
  scenes inside the first 10 s, and B-roll on screen for more than half of it. Here face beats may be as
  short as 1.5 s and B-roll scenes 2.3-4 s. Give these scenes the strongest pictures in the video (the
  big number, the real screenshot, the before/after), one bold element each, landing on its word with an
  SFX hit. Check the first 10 s on its own in the review step.
- **Face** (about 30-40%): personal stories ("like me", "as you can see"), jokes and swearing, rants and
  opinions, "I might get hate for this", the like/subscribe ask (always face), the outro. Outside the
  hook, face segments last at least about 2.5 s.
- **B-roll** (60-70%): anything that explains, lists, compares, shows a product, a process, a number, or
  a prompt. Consecutive B-roll scenes pan from one to the next, so long B-roll stretches are fine.
- Decide per B-roll scene whether the face square stays (the default) or goes (`{'face': False}`), see
  *The face square*.
- One scene covers one idea, about 5-15 s and roughly 19 spoken words. Longer is fine if parts are
  revealed in stages (a pipeline that grows box by box over 17 s worked well).
- **Every element lands on the word that names it** (`A(c, 'word')`). The scene's exit animation starts
  0.45 s before its end, so anchor nothing in the last ~0.8 s. A line must stay on screen 0.5 s plus
  0.3 s per word.
- Listicles: use `blocks.s_listcard` at each "Number N" (the list fills in, the current item gets a
  highlight, and a big circled number), a corner tag on that item's scenes, and a recap at the end.
  Non-list videos skip blocks entirely and still need some running structure of their own.

## The face square

build.py puts the user's face (`pip.mp4`, a centre crop of the face video made by setup.sh) in a rounded 360 px square at the
bottom right of B-roll scenes: x 1512-1872, y 672-1032. It pops in once the paper has slid in, shrinks
away just before the paper slides out, and stays put while back-to-back B-roll scenes pan. It sits above
the scene, so **anything drawn under it is hidden**.

- On by default. Turn it off for one scene with `{'face': False}` in that board entry:
  `(T('phrase'), s_dashboard, {'face': False})`. The user may name scenes that should be B-roll only;
  otherwise turn it off only when a scene truly needs the whole canvas (a full-width real screenshot whose
  detail is in the bottom right, a wide timeline or chart). Prefer redesigning the scene to fit.
- In scenes that show it, keep every element (text, image, logo, shape, arrow, ring) out of the corner
  **x > 1480 and y > 640**. Plan compositions around it: the free area is the full canvas minus that
  corner, so put wide rows and charts higher, end axes and arrows before x 1480 when they're low, and
  centre hero items left of the middle or high. Screenshots that fill the canvas width stop at y 640, or
  sit left of x 1480. `k.face` tells a scene whether the square is shown (blocks.s_listcard uses it to
  move its big number).
- layout.py reports anything in that corner as `under_face` (must fix). Scenes with `face=False` are
  not checked for it.
- The constants live in `sketch.py` (`PIP`, `PIP_CLEAR`). Previews (index.html: snap.sh, layout.py) show
  it as `<video>` clips from build.py; the final render draws it with ffmpeg in `plumbing/assemble.py`
  (same box, rounded border + shadow, same pop in/out). Change both if you change its look.

## Make it unique (not templated)

The style stays fixed: Excalidraw light, Excalifont, rough strokes, hatched pastels, dotted grid, and the
paper/pan transitions. **The compositions must come from this video's content.**

- Pick the **shape of the information** in each line first, then draw it. Shapes to choose from: a number
  that lands, a myth struck through and the truth written over it, before vs after, many things funnelled
  into a few, one chosen out of many, steps in order (a growing pipeline), a loop, a versus split, a
  timeline or race, a real screenshot with the detail circled, a typed prompt, a quote, a checklist, a
  decision fork, a chart line rising. Similar lines get different pictures.
- **Pictures over text.** The viewer already hears the words, so don't write them on screen. Each scene
  is carried by a picture. Real things (sites, apps, code, posts) are shown as the real asset, see *Real
  assets*; for everything else, draw the thing itself (the person, the laptop, the
  inbox, the clock, the pile of money, the robot doing the task) from `k.*` shapes, `D` doodles and
  logos. Text is only labels: a few words per label, about 12 words per scene at most, except where the
  text is the subject (a typed prompt, a quote, a hero number, a list card). If a scene is a title plus
  lines of text or labelled boxes, redraw it as a picture. When you draw something reusable (a person, a
  phone, a robot), add it to `$KIT/scripts/doodles.py` and the `D` list below so later videos get it.
- No two consecutive scenes share a layout. Don't fall back to "title + three boxes".
- Use **the user's real artefacts** where they talk about them: their logged-in pages, their own files
  (`outline.md`, repos), their usage pages, the actual product sites. This is what made the reference
  video feel personal.
- Don't reuse the reference video's layouts. Use it for API usage and pacing only. It predates the face
  square, so its scenes put things in the bottom-right corner that now has to stay empty.
- Facts: only numbers the user said, or values read from a real screenshot. If you draw something like a
  bar comparison without data, label it "(illustrative)". Don't soften the user's claims, and don't
  invent product names. When unsure which tool they mean (e.g. "an SEO skill"), draw it generically.

## Real assets

Look for these before drawing anything. The user reviews the video before it goes out, so use their own
material freely, but check every image for secrets and private data (keys, emails, other people's names,
client names, chat history) and crop or skip it. List what you used and cropped in the final report.

- **A video they mention** ("I made a video on this"): ask for the link if it isn't given. Then
  `curl -s "https://www.youtube.com/oembed?url=<watch url>&format=json"` gives the real title, and
  `https://i.ytimg.com/vi/<id>/maxresdefault.jpg` is the thumbnail. Show the thumbnail with `k.img` and
  write the real title, not the half-remembered one from the transcript.
- **Their own sites and products**: screenshot the live page with shoot.py.
- **Their skills, scripts and code**: find the real file (`fd`, `rg` under `~/Desktop/programming`, the
  project's `.claude/skills`) and show it as an image:
  `python3 scripts/plumbing/fileshot.py research/shots name=/path/to/file [name2=...]` (first 36 lines, editor look;
  `--cmd name="phantom --help"` shoots real command output as a terminal).
- **Their apps**: look for screenshots in the app's repo; otherwise ask the user for some.
- **GitHub**: `gh repo list` / `gh release list` find the repo; shoot the public repo or release page.
  A private repo's page needs Claude in Chrome, and the user's OK to show it.
- **Logged-in pages** (claude.ai chat, usage, dashboards): Claude in Chrome, cropped.
- **Tools they name** (HyperFrames, Excalidraw, ...): the tool's real site or logo.
- **Posts and embeds they mention** (a tweet, a Reddit thread, an article, a doc page): screenshot the
  real post, cropped to the post itself.
- Keep drawings for ideas that have no real-world picture: processes, comparisons, metaphors, numbers.
  A good scene often mixes both: the real screenshot with hand-drawn rings, arrows and labels on top.

## Screenshots

- Public page: write one `targets.json` for ALL the video's shots and run
  `cd broll && python3 scripts/plumbing/shoot.py assets/img targets.json` once. Each target can set
  `view` (viewport), `click` (texts or `css=` selectors, clicked in order; failures only warn), `scroll`
  (scroll that element near the top), `element` (shoot only that element, + `pad`) or `clip`, and `texts`
  to measure. The image is cropped at capture time (no ffmpeg crop), and `<name>.json` holds the measured
  boxes. Read the images once (a montage) to check content and privacy. Example:
  `{"side": {"url": "https://x.com/", "view": "1600x1400", "scroll": "css=input", "texts": ["Possible scam"]},
    "mcp": {"url": "https://x.com/about", "element": "css=#ai-assistants", "texts": ["Claude Code"]},
    "modal": {"url": "https://x.com/", "click": ["css=label:has-text('Applied')"], "element": "css=dialog[open]"}}`
  (`boxes.py` is the older measure-only version of this.) A plain `name=https://url` still writes
  `name.png` at 2000x1250 plus a `-tall` version.
- reddit.com: Claude in Chrome is blocked there. A public subreddit works with shoot.py
  (`https://www.reddit.com/r/<sub>/new/`; old.reddit needs a login); for anything logged-in use browsermcp.
- Logged-in or Cloudflare-blocked pages (Upwork search, claude.ai usage, dashboards): use Claude in Chrome.
  Run `tabs_context_mcp` (createIfEmpty), then `navigate`, then `computer screenshot` with
  `save_to_disk: true`. It returns a path to a 1410x840 jpg. Crop out the avatar, the name, the chat or
  sidebar history and anything else private with `ffmpeg -vf crop=w:h:x:y`. Close the tab when you're
  done.
- Show a screenshot with `s = k.shot(sid, 'assets/img/x.png', x, y, w=...)` (or `h=`): it draws the
  sketchy browser frame, sizes the other side from the PNG so nothing is cropped, and returns a Shot.
  Put rings, rects and marks on measured boxes: `k.ring('r', *s.r('Possible scam'), t=...)`; `s.pt(px, py)`
  maps any image pixel. Never place them by eye (that cost a fix round in the Reddit-jobs video).
  `k.img(...)` is the low-level version (you size it; object-fit crops a wrong aspect).

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
k.logo(sid, 'simpleicons-slug', x, y, size, t=, color=None)
k.raw(html)  k.js(code)  k.L(t_abs) -> scene time   k.g(op, args, **style) -> svg group for k.svg(sid, inner, t)
return k.done()
```

- `D` (doodles): `browser`, `code_file` (returns line positions you can ring), `file_icon`, `lightbulb`,
  `target`, `gauge`, `prompt` (typed into a Claude-orange box, with key SFX), `bubble`, `scroller`
  (an endless feed of cards), `counter` (counts a→b on the word), `sticky`, `bars`, `card` (a tiny post
  / job card: piles, feeds, before/after rows), `person` (stick figure, optional `sign=` placard),
  `phone`, `flag`, `trash`, `clock` + `pie` (shade a share of the hour).
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

- HyperFrames needs keyframes at most ~1 s apart or face clips freeze. The pipeline encodes `final.mp4`
  with `-g 30`, so setup.sh just copies its video stream. It re-encodes only an older `final.mp4` whose
  keyframes are seconds apart. build.py only puts face
  clips where the face is visible. One long `<video>` hits PLAN_TOO_LARGE (a 2 GB cap that can't be raised
  from the CLI).
- `npx hyperframes render --start/--end` still extracts all video frames and mixes the whole film's audio.
  Use `render.sh <broll> --test FROM TO` instead: it builds a render.html with only the windows touching
  that span (RENDER_SPAN), so a test costs what its frames cost.
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
- The face square is a second set of `<video>` clips (ids `pipNN`, track 3, z-index 3) placed only over
  B-roll runs, and animated from the main timeline in index.html (`core.CFG['main_js']`). They play
  `pip.mp4`, a 360x360 crop that setup.sh makes, never `face.mp4`: the renderer extracts video frames at
  the source size, and 1080p frames under every B-roll scene came to 4 GB, twice the PLAN_TOO_LARGE cap.
  This now only matters for previews: render.sh renders render.html, which has no face square and only
  the face footage inside B-roll windows (the paper slide edges).

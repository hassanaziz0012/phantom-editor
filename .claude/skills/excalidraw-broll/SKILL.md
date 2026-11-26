---
name: excalidraw-broll
description: Use when the user asks to create Excalidraw-style (hand-drawn, whiteboard, sketchy) B-roll for a YT video project, or to turn a talking-head final.mp4 into a face/B-roll cut. Replaces <project>/final.mp4 with a cut where full-screen HyperFrames B-roll alternates with the user's face, and the face stays visible in a rounded square at the bottom right during B-roll.
---

# Excalidraw B-roll for a talking-head video

The user records videos that are only their face talking to camera. This skill turns the face-only `final.mp4` into
a B-roll cut: **about 80% of the runtime is full-screen, light, Excalidraw-style B-roll** made with
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
- B-roll share is about 80% (80-85%; the user asked for this default in Nov 2026). Use quiet BGM (`Hello World.mp3` from `~/Videos/Asset Library/BGM`, or pass
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
  render rules in `scenes.md` are all you need (exception: `render.md`, *When something breaks*). Don't
  open or grep the kit source: `tools.py api [names]` prints every scene-API signature, `tools.py api --src
  NAME` one function's source (a pattern when you add a doodle).
- **Don't** read `reference/idle-agents-scenes.py` top to bottom. `grep -n "def s_"` it, then read one or
  two scenes if you need an example of the pattern. Look at `reference/look-1.jpg` once to see the look.
- Read the transcript once with `python3 scripts/tools.py plan` (numbered sentences with start times),
  plus `outline.md` if it exists. For exact timings collect ALL of a storyboard's anchors in one call:
  `tools.py find "phrase 1" "phrase 2" ...` (one line per phrase; hyphenated words match their parts).
  `tools.py words FROM TO` only for a gap you can't phrase. Never dump all 1500 words.
- Write `scenes.py` in 2-3 big `Write` calls (Read the template once first: Write refuses to overwrite a
  file that hasn't been read). Apply fix rounds as one Python string-replace script, not
  many small `Edit`s.
- Subagents keep bulky work out of your context: `broll-assets` (Haiku) fetches and checks the real
  assets, `broll-reviewer` (Sonnet) looks at the contact sheets. You get short text tables back. Don't
  re-do their image reads.
- Visual review: `layout.py` first (text output, no images), then one `snap.sh` pass for the reviewer,
  one fix pass, then re-snap only the scenes you changed.
- No test render for a normal video: the render path is proven. Render once, in the background, then do one
  spot-check Read of `research/final-sheet.jpg`. (Changing the kit's render path is different: test it with
  `render.sh <broll> --test FROM TO`, see step 6.)
- Don't poll a background job (setup, render, a subagent). You are notified when it finishes, and
  `tools.py` waits for setup's transcript by itself.

## Steps

Keep `broll/HANDOFF.md` updated after each step (the user wants this so a run can resume after rate
limits).

1. **Setup**: run `bash $KIT/setup.sh "<project>"` with `run_in_background` (about 2 min). It writes the
   transcript in its first seconds, so go straight on to step 2 in the same turn: `BROLL="<project>/broll"
   python3 $KIT/scripts/tools.py plan` waits for the transcript by itself. Don't sleep, poll or tail the log. It creates `<project>/broll/`
   (HyperFrames project, kit scripts, symlinked node tools, fonts, SFX + peaks), `voice.m4a`, `words.txt`
   and a looped BGM. It also makes `face.mp4`: a few-second stream copy of `final.mp4`, or a background
   re-encode (about 4 min) if `final.mp4` predates the pipeline's 1 s keyframes, plus `pip.mp4` (the face
   square's small video, about 1 min). Safe to re-run; on a project set up before the face square existed,
   re-running it just adds `pip.mp4`.
2. **Read and storyboard**: read the transcript (`tools.py plan`). Decide the face segments and the B-roll scenes
   (rules below). Each B-roll scene has a concept, an "information shape", its anchor words, and the real
   assets it needs. Write it to `broll/research/storyboard.md`, one entry per segment in board order (scene
   id, time window, FACE or concept, what must be visible incl. exact on-screen text and what each
   ring/arrow points at, face square on/off). The reviewer agent works from it, and it helps a resume.
2b. **Ask about real assets (one message, before writing scenes)**: list every scene where a real asset
   would beat a drawing, in two groups: the ones the agent can fetch (say from where) and the ones only
   the user can supply or point to (phone screenshots, private dashboards, a video link, a repo you can't
   find). Also list any name, domain or title you only know from the transcript and ask the user to
   confirm the spelling (speech-to-text gets these wrong: "watch.hustondev.me" was really
   watch.hassandev.me, "Excalibur" was Excalidraw). While you wait for the answer, start step 3 for the
   first group. An image the user pastes into chat is not saved anywhere you or the agent can use: ask for
   its file path instead (or let the agent produce it, e.g. a local app's UI, see *Real assets*).
3. **Assets: spawn the `broll-assets` agent (Haiku), don't fetch them yourself.** This covers every asset
   for the whole run, including ones that come up later (the user's 2b answer, a reviewer fix that needs a
   re-shoot or a crop, a screenshot of a local app's UI): SendMessage the same agent, even while it is
   still busy (the message is queued). Never fetch, render, crop or check one yourself. Write
   `broll/research/assets-request.md`, one entry per asset: name, what it should show and why (the scene),
   the source (URL, local path, YouTube link, logo slug, or "logged-in page: <where>"), the texts to measure
   for rings, and crop/privacy notes (for a recording, the record.py steps). Include every logo slug you
   plan to use; the agent checks them. Then call Agent with `subagent_type: "broll-assets"`, `model: "haiku"`, and a prompt
   giving the broll path and the request file (if that agent type isn't listed, use `general-purpose`
   with `model: "haiku"` and tell it to follow `.claude/agents/broll-assets.md`). Run it in the background while you plan the scenes. When
   the user's 2b answer brings more assets, append them to the request and SendMessage the same agent.
   It returns a table (also in `research/assets.md`): file, size, what each image shows and where the
   key detail is, missing texts, privacy crops, failures. Work from that table. Don't Read the asset images
   yourself, not even to double-check privacy: the agent's privacy check is final (the user reviews the
   video too). When the user asks for stricter privacy, put it in the request or SendMessage it to the
   agent. (Exception: one Read of `research/montage-assets.jpg` if a composition really depends on how an
   image looks.) Turn failed assets into drawings, or ask the user.
4. **Write `broll/scripts/scenes.py`** from the template that is already there: `board()` plus one function
   per scene. First Read `/home/hassan/Desktop/programming/phantom-editor/.claude/skills/excalidraw-broll/scenes.md` once (the scene API, using screenshots, render-safe JS, motion,
   gotchas) and run `python3 scripts/tools.py api doodles` once for the doodle signatures (the `k.*` ones are
   in scenes.md; `tools.py api NAME` for any other exact signature). Then run `python3 scripts/tools.py anchors` (prints every in-scene phrase that isn't found, falls
   outside its scene, or lands in the last 0.8 s; silent = good; fix all before building), then
   `python3 scripts/build.py && python3 scripts/tools.py share`. share prints the
   B-roll % and flags scenes shorter than 2.3 s. Adjust the board until the split is about 80% (80-85%).
5. **Review**: first the geometry, by script: `python3 scripts/plumbing/layout.py` (about 15 s; pass scene
   ids like `s05 s12` to re-check only those). It opens every B-roll scene in a browser and reads where
   each element really lands, across the whole scene. Fix everything it prints: `off_canvas`,
   `outside_safe`, `under_face` (something sits under the face square), `text_overlap`, `label_spills`,
   `crosses_box` (a text half in, half out of a box),
   `logo_overlap`, `small_text`, `never_shown` and `late` (anchored too close to the scene's end, or past
   it). `text_on_image` is fine when it is a callout you put there on purpose. Then the look, by eye, **done
   by the `broll-reviewer` agent (Sonnet), not you**: run `bash $KIT/snap.sh "<project>/broll"`, then call
   Agent with `subagent_type: "broll-reviewer"`, `model: "sonnet"`, and a prompt giving the broll path, the snap folder and
   `research/storyboard.md` (update the storyboard first if scenes changed while writing them; if the agent
   type isn't listed, use `general-purpose` with `model: "sonnet"` pointed at
   `.claude/agents/broll-reviewer.md`). Don't Read the contact sheets yourself. It returns one line per
   problem (scene | what | fix): wrong text, weak or mostly-text pictures, rings or arrows on the wrong thing,
   off-centre elements, repeated layouts, a weak hook. Apply the fixes you agree with in one replace
   script. Then run
   `python3 scripts/tools.py check` (13 s). Fix errors and `line_unread` warnings (a line that isn't on
   screen long enough to read). After fixes, re-run layout.py on the changed scenes, re-snap only
   those: `bash $KIT/snap.sh <broll> snap2 "t1,t2,..."`, and SendMessage the same reviewer the snap2 folder
   plus the scene ids in frame order.
6. **Render**: run `bash $KIT/render.sh "<project>/broll"` with `run_in_background` (15-30 min; you are
   notified when it ends, don't poll). It renders, checks every frame (QC), delivers the cut as
   `<project>/final.mp4` (the face-only original becomes `final-face.mp4`), prints duration and loudness and
   writes `research/final-sheet.jpg`. Read the sheet once. **Only if** QC fails (exit 1), the render breaks, or
   you change the kit's render path, Read `/home/hassan/Desktop/programming/phantom-editor/.claude/skills/excalidraw-broll/render.md` first. When the user asks how far along it is:
   `python3 $KIT/progress.py "<project>/broll"`.
7. **Report**: give the path, the B-roll %, and anything the user should check: illustrative numbers,
   claims you hedged, which real assets you used, screenshots of their private pages and what you
   cropped out, and any scene that is still a drawing because the real asset couldn't be had.

## Storyboard rules

The hook treatment and the ~80% B-roll share are defaults for when the user says nothing. If they ask
for something else for this video (a raw intro, 60% B-roll, much less B-roll), do that instead and scale
the other rules to fit. `tools.py share` only reports the split; it is not a target to hit then.

- **The hook (first 10 s) is the most heavily edited part of the video.** By default, don't leave it as
  raw face: the user wants the B-roll to grab attention from the first seconds. Open on the face for at most about 2 s
  (just enough to establish the person), then cut fast between B-roll and face: at least two B-roll
  scenes inside the first 10 s, and B-roll on screen for more than half of it. Here face beats may be as
  short as 1.5 s and B-roll scenes 2.3-4 s. Give these scenes the strongest pictures in the video (the
  big number, the real screenshot, the before/after), one bold element each, landing on its word with an
  SFX hit. Check the first 10 s on its own in the review step.
- **Face** (about 20%): the like/subscribe ask (always face), the outro, punchlines ("it's not even close"),
  jokes and swearing, and the sharpest bits of a rant. That is not enough to keep every opinion on the face:
  most opinions, personal stories and "comment below" asks get a picture too (a balance scale, a comment box,
  a grid of refused requests), with the face square still showing. Outside the hook, face segments last at
  least about 2.5 s.
- **B-roll** (about 80%): anything that explains, lists, compares, shows a product, a process, a number, or
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
  phone, a robot), add it to `$KIT/scripts/doodles.py` with a one-line docstring (signature + where its
  anchor point is), so later videos get it and `tools.py api` lists it.
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

Look for these before drawing anything, and request them from the `broll-assets` agent. The user reviews
the video before it goes out, so use their own material freely. The agent checks every image for secrets
and private data (keys, emails, other people's names, client names, chat history) and crops or flags it;
its check is final. List what you used and what it cropped (from its table) in the final report.

- **A video they mention** ("I made a video on this"): ask for the link if it isn't given. The agent gets
  the thumbnail and the real title; write the real title, not the half-remembered one from the transcript.
- **Their own sites and products**: screenshot the live page with shoot.py.
- **Their skills, scripts and code**: the real file shown as an editor image (first 36 lines), or real
  command output as a terminal (`phantom --help`). Give the agent the path if you know it.
- **Their apps**: the agent looks for screenshots in the app's repo, and otherwise renders the app's own
  frontend from the repo with a stubbed backend (how the Rambler status page was shot). Give it the repo
  path and the state to show. Ask the user only if both fail.
- **GitHub**: `gh repo list` / `gh release list` find the repo; shoot the public repo or release page.
  A private repo's page needs Claude in Chrome, and the user's OK to show it.
- **Logged-in pages** (claude.ai chat, usage, dashboards): Claude in Chrome, cropped.
- **Tools they name** (HyperFrames, Excalidraw, ...): the tool's real site or logo.
- **Posts and embeds they mention** (a tweet, a Reddit thread, an article, a doc page): screenshot the
  real post, cropped to the post itself.
- Keep drawings for ideas that have no real-world picture: processes, comparisons, metaphors, numbers.
  A good scene often mixes both: the real screenshot with hand-drawn rings, arrows and labels on top.

## Other files (Read them only when the step says so)

- `scenes.md`: at step 4, once. Scene API cheat sheet, using screenshots, render-safe JS/HTML, motion craft,
  scene-writing gotchas.
- `render.md`: only when QC fails, the render breaks, or you change the render path or the kit. Render
  details, QC, test renders, kit internals and their gotchas.

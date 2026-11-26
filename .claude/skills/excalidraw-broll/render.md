# Excalidraw B-roll: render, QC and kit internals

Read only when QC fails, the render breaks, or you change the render path or the kit (SKILL.md step 6).

## Render details

**Render**: run `bash $KIT/render.sh "<project>/broll"` with `run_in_background`. Chrome only renders
   the B-roll windows (`render.html`, packed back to back, no face square, no audio); `assemble.py` then
   rebuilds the film with ffmpeg (face gaps from face.mp4, face square from pip.mp4, voice + BGM + SFX).
   It waits for the face encode, delivers the result as `<project>/final.mp4` (the face-only original
   becomes `final-face.mp4`), prints duration and loudness, writes `research/final-sheet.jpg` (24 frames)
   and prints a timing table per stage (wait-face, build, chrome, assemble, verify, qc, sheet, deliver), also
   appended to `research/render-timings.log`. Read the sheet once. If something is wrong, fix and re-render.
   **QC** (`scripts/plumbing/qc.py`, ~1.5 min) checks every frame of the cut before delivery: black frames,
   the face frozen outside B-roll, and the face a frame off `face.mp4` at each B-roll cut (a stutter). On a
   failure render.sh does NOT deliver: it prints the film times, keeps `renders/broll.mp4`, writes
   `research/qc.txt`, and exits 1. Report it to the user and find the cause; `QC_FORCE=1` delivers anyway
   (only when the user says so). Run it by hand on any cut: `cd <broll> && python3 $KIT/scripts/plumbing/qc.py ../final.mp4`.
   Measured on the Reddit-jobs video (7 min, 86% B-roll): chrome 522 s (370 s of packed B-roll, ~43 s of
   video per minute), assemble ~290 s (about 1.5x realtime), total ~14.5 min (the old all-in-Chrome render
   took 48 min). Time scales with B-roll seconds + film length. `--test FROM TO` renders just that span to `renders/test.mp4` (not delivered) with its own
   `research/test-sheet.jpg`. After changing the render path, render the full film with `--test 0 <end>` and
   run `tools.py compare renders/test.mp4 ../final.mp4` against a known-good cut (in sync: mean SSIM ~0.99+).
   A 20 s test once passed while the full film drifted 30 s.
   **Progress**: when the user asks how far along it is, run `python3 $KIT/progress.py "<project>/broll"`
   (one line: stage, % done, speed, minutes left; about 0.2 s, safe to repeat). render.sh also refreshes
   `research/progress.txt` every 30 s, so the user can `cat` it. Don't count frames or read logs by hand.

## The face square in the kit

- The constants live in `sketch.py` (`PIP`, `PIP_CLEAR`). Previews (index.html: snap.sh, layout.py) show
  it as `<video>` clips from build.py; the final render draws it with ffmpeg in `plumbing/assemble.py`
  (same box, rounded border + shadow, same pop in/out). Change both if you change its look.

## When something breaks that SKILL.md and these files don't cover

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
- The face square is a second set of `<video>` clips (ids `pipNN`, track 3, z-index 3) placed only over
  B-roll runs, and animated from the main timeline in index.html (`core.CFG['main_js']`). They play
  `pip.mp4`, a 360x360 crop that setup.sh makes, never `face.mp4`: the renderer extracts video frames at
  the source size, and 1080p frames under every B-roll scene came to 4 GB, twice the PLAN_TOO_LARGE cap.
  This now only matters for previews: render.sh renders render.html, which has no face square and only
  the face footage inside B-roll windows (the paper slide edges).
- Root-level clip times (face clips, scene hosts, face squares) go through `core.clip_span(t0, t1)` and
  `core.media_start(t)`, never a hand-written `"%.3f"`. Edges rounded to ms on their own landed just after their
  frame: back-to-back face clips left one frame no clip covered (a black flash at a window start: Chinese-models
  10:51 and 7:14, end-of-session 0:37). HyperFrames also floors the media time, so a media start rounded down
  (621.333 for 621.3333) showed the previous frame: the face inside a window ran a frame behind face.mp4, a
  stutter at the cut. build.py fails with "covered by no clip" if render.html still has an uncovered frame.
- `tools.py check` hides two expected findings (`EXPECTED` in tools.py): `multiple_root_compositions`
  (build.py writes index.html + render.html on purpose; render.sh passes `-c render.html`, Chinese-models video,
  Nov 2026) and `nested_media_start_basis_ambiguous` (`k.video` clips use scene-local `data-start`; verified in
  the packed render, Fable 5.2 video).
- QC once failed on a freeze that was in the recording itself (Singularity video, film 855.77, Nov 2026; an
  earlier video too). qc.py now re-checks each face-only freeze against `face.mp4` at the same film time and
  only notes it ("source freeze ... ignored") when the source is frozen there too.

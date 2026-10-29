# HyperFrames B-roll Plan

Source of truth for the reusable B-roll feature. Start new chats from this file.

## Status (2026-10-30)

| Piece | State |
| --- | --- |
| `step-card` template | **Done.** Elastic length, dark/light theme, colour and font variables, label pattern, auto-fit title |
| `step-card-hassandev` template | **Deleted** (not wanted). Themes are now variables inside one template |
| `big-stat` template | **Done** (2026-10-29). Odometer count-up of any `value` string ("$3.4B", "19,000", "1.3 billion"), label, kicker, source, centre/left layout, themes, fonts |
| `terminal` template | **Done** (2026-10-29). Typed command, output lines on beats, fresh prompt, optional flag badge + highlighted part, themes, fonts |
| `flow` template | **Done** (2026-10-30). 2–6 boxes, arrows draw and steps light on beats, optional loop-back arrow + label, notes, numbering, auto row/column layout, themes, fonts |
| `checklist` template | **Done** (2026-10-30). 1–6 items, tick or cross lands on each beat, mixed `[do]`/`[dont]` lists, `code` chips, reveal each/all, optional strike, title + kicker, themes, fonts |
| Wrapper (`phantom broll`) | **Done.** Finds timing in the captions, patches the length, renders, writes placement info |
| End-to-end test | **Passed** on "everything is falling apart" (see [Test record](#test-record)) |
| Docs | `docs/broll.md` (mkdocs site), `broll-templates/README.md` (per-template variables) |
| Committed to git | **Yes** (2026-10-30): `broll-templates/` (all three templates + `broll.py`), the `phantom broll` CLI case, `docs/broll.md`, `mkdocs.yml` and this file. `.hyperframes/` and `renders/` inside templates are git-ignored |
| Re-verified | 2026-10-30: `check` passes on all three; real `phantom broll clip` renders of `big-stat` (4 s) and `terminal` (6 s, flag + highlight + output) look right |
| Next | **Test all five templates on the video recorded 2026-10-30** (first real use of `big-stat`, `terminal`, `flow`, `checklist`). Then the overlay step and the `organize-recordings` skill step. More templates after that (Versus split next) |

## Goal

The `hyperframes` skill is expensive in tokens. Keep using it for one-off, video-specific
B-roll, but also build a small library of **reusable B-roll templates** that are:

- authored **once** with the skill,
- customised per video through **variables** (text, numbers, colours, style),
- rendered with **one CLI command** that costs zero LLM tokens,
- simple enough that a cheap model (Haiku) can pick the cue, write a small JSON and
  run the command.

## Cost model

| Step | Tokens |
| --- | --- |
| Authoring a template with the hyperframes skill | Real cost, once per template |
| `phantom broll render` (wraps `npx hyperframes render`) | Zero. Local headless browser + ffmpeg, ~30 s CPU per clip |
| Haiku choosing cues and writing a cue JSON | Small. It reads `trimmed.srt` (or a summary), never the word-level SRT: the script does that lookup |

## How to use it (per video)

1. Pick the moments for B-roll from `trimmed.srt`. Check a phrase if unsure:
   `phantom broll find "two factor authentication" --srt trimmed-1word.srt`
2. Write `broll/<name>.json` in the video project:
   ```json
   {
     "defaults": {"template": "step-card", "variables": {"total": 4, "theme": "dark"}},
     "clips": [
       {"file": "step-1.mp4", "cue": "So number one", "until": "on all these different sites",
        "variables": {"number": 1, "title": "Change weak & reused passwords"}},
       {"file": "step-2.mp4", "cue": "set up two-factor authentication", "until": "software that you use",
        "variables": {"number": 2, "title": "Turn on 2FA everywhere"}}
     ]
   }
   ```
3. `phantom broll render broll/<name>.json --dry-run` to check the timing (instant).
4. `phantom broll render broll/<name>.json` to render. Re-runs skip unchanged clips.
5. Use `broll/<name>.resolved.json` (written next to the cue file) to place each clip:
   `at` (seconds into the final video), `duration`, `end`, `file`.

`trimmed-1word.srt` is found automatically in the cue file's folder or one or two folders up,
so a cue file in `<project>/broll/` needs no `--srt`. Other commands: `phantom broll list
[template]` (variables, types, allowed values), `phantom broll clip <template> -o out.mp4
--cue … --until … --var key=value` (one clip without a cue file). Full reference:
`docs/broll.md`.

## What exists

### `broll-templates/step-card/` (HyperFrames project)

A numbered point card: big rolling number on the left, a small label ("STEP 1 OF 4") and
a bold title on the right.

| Variable | Type | Default | Notes |
| --- | --- | --- | --- |
| `number` | number 1–99 | 1 | Shown 2-digit ("01") |
| `total` | number 0–99 | 4 | Only used through `{total}` in `label` |
| `label` | string ≤40 | `Step {n} of {total}` | Pattern: `Red flag #{n}`, `#{n} of {total}`, `Automation Tip #{n}` |
| `title` | string ≤80 | | Auto-fits 112px → 56px, max 3 lines |
| `theme` | enum | `dark` | `dark` or `light` |
| `color-ground`, `color-ink`, `color-muted`, `color-accent` | color | dark palette | Left at default = follows the theme. Any other value overrides in both themes |
| `font` | enum | Inter | Number + title. 13 bundled fonts (Montserrat, Oswald, League Gothic, Playfair Display, …) |
| `font-label` | enum | IBM Plex Mono | Label. 9 bundled fonts |

Motion: a fixed ~1.2 s entrance (digits roll down out of a blur, label and title words fade
in), then a slow push-in (up to 4%) across the rest of the clip, last 6 frames still, then
a hard cut. There is **no exit animation** any more (the old blur-to-black exit was removed
to follow the hard-cut house style; easy to add back if wanted).

Known nits: `check` warns `first_frame_empty` (frame 0 is just the background, because
everything enters from nothing; kept on purpose). With wide fonts like Montserrat the two
digits touch slightly.

### `broll-templates/broll.py` (the wrapper, run as `phantom broll`)

Wired into the `phantom` bash CLI (`broll)` case) and installed with `./install.sh`.
Uses `rapidfuzz` (already a project dependency) and `parse_srt` / `parse_timestamp` from
`video-editing/utils.py`.

What a render does, per clip:

1. **Load the template**: read `data-composition-variables` and the default length from
   `index.html`, and the pinned HyperFrames version from `package.json`.
2. **Validate variables** against their declared types (number range, enum options, hex
   colour, string max length). Errors stop everything before any render time is spent.
3. **Resolve timing** from `trimmed-1word.srt`:
   - start = first word of `cue` (or `at` timestamp), plus optional `offset`;
   - end = last word of `until`, searched up to 90 s after the start (or `duration`, or the
     template's default length);
   - `after` = only look for the cue after this timestamp (for phrases said twice);
   - `beats` = phrases or seconds, resolved in order, sent as `"0.000,1.800,…"` (only for
     templates that declare a `beats` variable);
   - rounded to whole frames (30 fps); under 1 s is an error, over 30 s is a warning.
4. **Fuzzy matching**: words are lowercased, punctuation dropped, hyphenated words split
   ("two-factor" → "two factor"). Every window of n-1, n and n+1 words is scored with
   `fuzz.ratio`; the earliest match within 5 points of the best wins; threshold 75
   (`--min-score`). If the cue also matches strongly elsewhere it prints a warning naming the
   other timestamps.
5. **Patch the length**: copy the template to a temp dir, set `data-duration` on the
   composition root (and on any element marked `data-broll-span`). The value is truncated to
   milliseconds, not rounded, because HyperFrames rounds partial frames up (7.667 s became
   231 frames instead of 230).
6. **Render**: `npx --yes hyperframes@<pinned> render <tmp> --variables-file … --strict-variables
   --quality looks -o <file>`. Also injects `duration` (if the template declares it) and `beats`.
7. **Write placements** to `<cue file>.resolved.json`, sorted by time and merged with
   earlier runs. Each clip has a `hash` of template files + variables + length + quality;
   clips whose hash and output file are unchanged are skipped unless `--force`.

Cue file keys per clip: `template`, `file` (relative to the cue file), `variables`, `cue` |
`at`, `until` | `duration`, `after`, `offset`, `beats`. Top level may be a plain list or
`{"srt": …, "defaults": {…}, "clips": […]}`; `defaults.variables` merge into each clip's.

### Other context

- Old hand-placed renders and cue files from the first real use:
  `~/Videos/YT Projects/everything is falling apart/broll/` (`step-cards.json`,
  `step-cards-hassandev.json` and their MP4s; made before this work, left untouched).
- One-off B-roll brief with house style rules (hard cut in/out, no audio, 96px safe
  margin, min text 44px, headline 96px+, no real people/logos):
  `~/Videos/YT Projects/Learn by shipping: The fastest way to learn coding in 2026/broll-prompts.md`.
- Every video project has `trimmed.srt` (captions as they appear in the final video) and
  `trimmed-1word.srt` (word-level timestamps). Cue timing comes from the 1-word file.

## Test record

- **`step-card`**: checked at 2 s, 2.5 s, 3.2 s, 5 s and 15 s; short ("Use 2FA") and long
  (80-character, 3-line) titles; both themes; Inter, Oswald, Playfair Display, Montserrat;
  a custom accent colour; `npx hyperframes check` passes (only the `first_frame_empty` warning).
- **`big-stat` and `terminal`** (2026-10-29, re-checked 2026-10-30): `check` passes
  (`big-stat` has the same `first_frame_empty` warning; `terminal` has none, its window is on
  screen at frame 0). Real renders through `phantom broll clip --at 0 --duration N` at 4 s and 6 s.
- **`flow` and `checklist`** (2026-10-30): `check` passes (`flow` warns only about file size,
  `checklist` is clean). Rendered through `phantom broll` at 2 s, 5–7 s: default, agent loop
  (loop + label + numbers + title), notes under labels, 6 long labels in light theme (auto
  switched to column, fits the safe area); checklist default, all-cross with strike, red flags
  with `code` chips and `reveal: all`, a mixed 6-item light list, a 2 s one with no title.
  Numeric beats checked frame by frame (steps and marks land on the beat); phrase beats
  resolved against "everything is falling apart" in a dry run.
- **Wrapper, end to end** on "everything is falling apart" (outputs went to a scratch folder,
  not the project):

  | Clip | Resolved start | Old hand-placed `at` | Length | Frames |
  | --- | --- | --- | --- | --- |
  | step-1 | 1:41.67 | 1:41.68 | 7.67 s | 230 |
  | step-2 | 2:50.33 | 2:50.33 | 6.83 s | 205 |
  | step-3 | 3:59.73 | 4:00.07 (different cue wording) | 6.33 s | 190 |
  | step-4 | 4:59.70 | 4:59.71 | 9.17 s | 275 |

  ~30 s render per clip; re-run with nothing changed takes 0.4 s. Typos ("so numbr one"),
  ambiguous cues ("two factor" said twice → warning), no match (error with closest
  candidate), bad variables and `--only` / `--force` were all exercised.

## Writing a new template (rules learned so far)

Everything `broll.py` needs from a template, plus HyperFrames gotchas hit while building
`step-card`:

1. **Length**: read it at runtime from the root, e.g.
   `Number(document.getElementById("root").getAttribute("data-duration"))`. Don't put
   `data-start` / `data-duration` on anything else unless it must span the clip, in which case
   add `data-broll-span` so the wrapper patches it too.
2. **Shape**: fixed entrance → elastic hold with gentle ambient motion → last ~6 frames
   still → hard cut (no exit). Must look right at 2 s and at 20 s.
3. **Theming**: a `theme` enum plus colour variables whose defaults are the dark palette;
   a colour still at its default follows the theme. Apply colours through **private** CSS
   variables (`--sc-ground` etc.) set from script: the HyperFrames runtime writes the
   `--<variable-id>` properties itself after your script runs and overwrites any change to them.
4. **Fonts**: offer an `enum` of bundled fonts (Inter, Montserrat, Outfit, Nunito, Oswald,
   League Gothic, Archivo Black, Space Mono, IBM Plex Mono, JetBrains Mono, EB Garamond,
   Playfair Display, Source Code Pro, Roboto, Open Sans, Lato, Poppins). Name every option
   in the CSS (a hidden `#sc-fonts` element in `step-card`), or the compiler won't bundle it.
   Wait for `document.fonts.load(...)` before measuring text, and register the timeline at
   the end of that async build.
5. **Text fit**: measure once before building tweens; try sizes from large to small until it
   fits the line limit with no word wider than the column.
6. **Numerals**: `font-variant-numeric: lining-nums tabular-nums`, or old-style figures
   (Playfair) drop below a 1em mask.
7. **Lists and beats**: no array type, so pass delimited strings (`"rm -rf|sudo|curl | bash"`,
   beats as `"0,1.8,4.2"`) and declare a `beats` string variable if the template uses beats.
8. **Testing without the wrapper**: `snapshot` can't take variables, so make temp copies with
   patched defaults and duration and snapshot those. Two traps: the first `--at` capture comes
   back blank (capture an extra `0` first and drop it), and `compare` doesn't bundle fonts, so
   every variant shows the fallback font there.
9. **Optional extras default to empty** (a flag, a highlight, a source line), so a clip only
   gets them when its cue file asks. Never make a warning or decoration the default.
10. **`fromTo` renders its "from" at build time** (`immediateRender` defaults to true). Two
    `fromTo`s on the same element at different times: the later one's "from" paints frame 0.
    Pass `immediateRender: false` on every `fromTo` after an element's first, or the colour
    states of later steps show at frame 0 (hit in `flow`).
11. **Tracking per font**: tight negative letter-spacing suits Inter/Montserrat but crushes
    condensed fonts (Oswald, League Gothic) and serifs; set it per font option (`big-stat` has a
    `TRACK` table).

## HyperFrames variables (facts from the skill docs)

- Declared on the root `<html>` as `data-composition-variables`, an array of
  `{id, type, label, default}`. Types: `string` (`placeholder`, `maxLength`),
  `number` (`min`, `max`, `step`, `unit`), `color`, `boolean`, `enum` (`options`).
- Binding: `data-var-text="id"` for text, `data-var-src="id"` for media, `var(--id)` in
  CSS, `window.__hyperframes.getVariables()` once at init for logic.
- Rendering:
  ```bash
  npx hyperframes render --variables '{"title":"…"}' -o out.mp4 --strict-variables
  npx hyperframes render --variables-file vars.json -o out.mp4
  npx hyperframes render --batch rows.json --output "renders/{name}.mp4"   # {"rows":[{...},{...}]}
  ```
  Missing keys fall back to defaults. `--strict-variables` fails on unknown keys or type
  mismatches. Don't combine `--batch` with `--variables`/`--variables-file`.
- Variables can't change the root's `data-duration` (hence the wrapper's patching).
- Studio's Variables panel lets you tweak values by hand (zero tokens).
- Skill docs: `~/.claude/skills/hyperframes/references/format.md` (Variables section),
  `~/.claude/skills/hyperframes-cli/references/preview-render.md`,
  `~/.claude/skills/hyperframes-cli/references/compare-and-batch.md`.

## Timing principles

1. **Elastic duration** instead of fixed-length clips (see the template rules above).
2. **Beats**: multi-item templates take beat times relative to clip start, so each list
   item / node / headline lands on the spoken word.
3. **Never time-stretch a render** (ffmpeg `setpts`): it makes entrances sluggish.
   Re-render at the right length instead.
4. B-roll doesn't have to cover the whole segment. Cutting back to the face for the
   remainder is fine and often better.

## Template backlog (from the transcripts of all 16 videos)

Ordered roughly by how often the pattern comes up.

1. ~~**Numbered point card**~~ Done as `step-card`.
2. ~~**Big stat.**~~ Done as `big-stat`. Number counts up to its value, label underneath, optional source line.
   E.g. 70% of websites, 1.3 billion tokens, Meta's $900M on Claude, Uber's $3.4B budget
   gone in 4 months, 99% of engineers, 19,000 lines, 95% of weekly limit, 200 req/hour.
3. ~~**Terminal command.**~~ Done as `terminal`. Fake terminal types a command, optional output lines, optional
   red danger flag + label. E.g. `rm -rf`, `sudo`, `curl … | bash`,
   `--dangerously-skip-permissions`, `phantom pipeline process --raw`.
4. **Versus split.** Two sides with a value/label each, optional winner highlight or
   result ("5x faster"). E.g. $5,200 vs $260 (API vs cached), senior vs junior with AI,
   LLM vs harness, prompt vs semantic caching, 1 CPU core vs 4, Lovable/Bolt vs Claude Code.
5. ~~**Flow / pipeline.**~~ Done as `flow`. 3–6 labelled boxes, arrows light up on beats, optional loop-back
   arrow. E.g. the agent loop, user → app → vector DB → LLM,
   track → find → automate → repeat, record → fix mistakes → process → publish.
6. ~~**Checklist (do / don't).**~~ Done as `checklist`. Items tick ✓ or cross ✗ on beats. E.g. the 4 command red
   flags; architecture / performance / security; "don't automate: one-offs, sensitive
   tasks, creative work".
7. **Quote / emphasis card.** Styles via an `enum`: plain quote, comment card (for
   Twitter-drama videos, generic styling, initials avatar, no pixel-perfect platform UI),
   and claim + FALSE/WRONG stamp for myth-busting openers ("coding is solved",
   "learn by hand first", "learning is obsolete").
8. **Definition card.** Dictionary-style term + plain-English meaning. E.g. WSL, sudo,
   embeddings, agent harness, cache hit/miss, O(n), I/O-bound, parallelization.
9. **Meter / gauge.** Bar fills to X%, optional threshold line. E.g. weekly quota 95%,
   context window 30/25/75%, model scores dropping below a 90 cutoff.
10. **Headline stack.** Plain-text news cards land one per beat (PHP, GitLab, OpenCode,
    Railway, model releases). Generic styling only, no real outlet logos or branding.
11. **Extras:** chapter title card; subscribe / "link in description" overlay (needs
    alpha output, see open questions).

## Build order

1. ~~Upgrade `step-card`: elastic duration, prefix label, auto-fit title.~~ Done, plus a
   light theme and font/colour variables.
2. ~~Write the wrapper and prove it end to end on "everything is falling apart".~~ Done:
   `phantom broll`.
3. ~~Build **Big stat** and **Terminal command** templates.~~ Done as `big-stat` and `terminal`.
4. ~~Build **Flow** and **Checklist**.~~ Done as `flow` and `checklist` (2026-10-30).
5. Test all five on a real video (recorded 2026-10-30), then the two pending steps below.
6. Then Versus, Quote, Definition, Meter, Headline stack as needed.

Pending (deferred, not dropped):

- Add the B-roll step to the `organize-recordings` skill so Haiku can do it per video
  (write the cue file from `trimmed.srt`, `--dry-run`, render).
- Build the overlay step: a pipeline step reads `<cue file>.resolved.json` and cuts each clip
  into `final.mp4` automatically (decided 2026-10-29).

## Template acceptance checklist

- [ ] All copy, colours and style choices are variables; lists/beats are delimited strings.
- [ ] Length read at runtime; nothing else timed (or marked `data-broll-span`); looks right
      at 2 s and at 20 s.
- [ ] Auto-fit text; checked with the shortest and longest realistic values.
- [ ] Frame 0 is already a readable composition; last ~6 frames hold; no audio.
- [ ] 1920x1080, 30 fps, H.264; 96px safe margin; text ≥ 44px, headline ≥ 96px.
- [ ] `theme` enum (dark/light), colour variables that follow the theme at their defaults,
      font enums; colours applied through private CSS variables.
- [ ] `npx hyperframes check` passes; `phantom broll clip <template> --dry-run` and one real
      render work.
- [ ] Section added to `broll-templates/README.md` with variables and usage.
- [ ] Row added to the status table and backlog in this file; committed.

## Open questions

- **`phantom broll clip` without a cue** needs `--at 0` even when you only want a standalone
  clip (`--duration` alone errors with "needs 'cue' or 'at'"). Could default to `at 0`.

- **Alpha output** for overlay templates (subscribe prompts etc.): `render --format mov`
  (ProRes 4444) or `--format webm` keep transparency, per `render --help`. Not tried yet,
  and `broll.py` always renders MP4 today.
- **`first_frame_empty`**: the checklist wants frame 0 readable, but `step-card`'s entrance
  starts from nothing. Keep as is, or show the card at frame 0?

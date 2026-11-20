# Handoff: cutting tokens in the excalidraw-broll skill (2026-11-17)

Read this, then SKILL.md, then talk the plan through with the user before you edit anything. The user wants
to discuss and agree on the plan first, especially the subagent design.

## Where things stand

- Last run: "13 everyday tasks your agent does better than you" (15:28 film, 102 board entries, 86% B-roll,
  13 list cards). Delivered and approved ("video looks fine"). It cost **250k+ tokens** in one session, about
  the same as the first build (~300k), which is what the skill's token rules were written to avoid.
- The user asks for token ideas at the end of every run. "No more ideas" is an acceptable answer.

## Where the tokens went (13-tasks run, estimates)

| Part | Rough cost | Notes |
|---|---|---|
| Writing `scenes.py` (~1,100 lines, 3 writes + fix scripts) | ~35-40k output | The creative core; unavoidable in some form |
| Image reads: 9 contact sheets + 3 re-snaps + ~5 asset montages/crops + final sheet | ~15 images | Each image stays in context and is re-sent with every later call |
| Kit source reads (doodles.py, blocks.py, sketch.py, reference scenes) | ~10k | **Fixed** (see below) |
| Transcript `plan` + one big `find` for all anchors | ~15k | Fine; already efficient |
| Fix rounds: anchors outside their scene (~14), labels on screenshots (~6), visual fixes | 3 build/check rounds | Anchor ones **fixed** |
| Asset improvising: hero page shot mid-animation, guessed crop coordinates, logo check, hand-made montage | ~10 extra calls | Partly **fixed** |

**The main multiplier is context growth, not any single step.** Everything read early (images, kit source,
the transcript, the scenes code) is re-sent with every later call. That is why moving bulky work into
subagents, which return only a short text summary, should pay off more than the cheaper per-token price.

## Already done this session (in the kit and SKILL.md)

1. Signatures for all 24 doodles in the SKILL.md cheat sheet, so agents no longer open doodles.py.
2. `tools.py anchors`: checks every in-scene `A()`/`at()` phrase against its scene window before the first
   build (NOT FOUND / BEFORE / AFTER / LATE). Tested.
3. shoot.py targets take `"wait": seconds`, for animated hero pages.
4. `tools.py montage img...`: one grid image for the privacy check.
5. Also fixed: list cards scale to 10-13 items, the big list number is centred on its glyph (the user caught
   it off centre), `kit/assets/img/blank.png` is shipped, and there are new doodles (robot, envelope, folder,
   magnifier, monitor, shield).

Ideas I proposed that the user did NOT pick yet: `tools.py logos slug...` (check simple-icons slugs), moving
`clockpie` / `clip` / `grid200` helpers from the 13-tasks scenes.py into doodles.py, and having `k.shot`
return its bottom edge so labels don't land on screenshots.

## The proposal to discuss: subagents

The user explicitly asked about running cheaper models (Haiku etc.) as subagents for token-heavy work that
still needs an LLM. Proposal so far, not agreed yet:

| Step | Subagent? | Model | Returns to main |
|---|---|---|---|
| 1 Setup, 6 Render | No (scripts) | - | - |
| 2 Storyboard, 2b asking the user | **No**, main model | - | - (creative decisions + user contact) |
| 3 Assets: targets.json, shoot.py, fileshots, montage, privacy check | Yes | **Haiku** (mechanical, easy to check) | paths, sizes, measured boxes, privacy notes, failures |
| 4 Writing scenes | Maybe: 3-4 in parallel, one chunk of the storyboard each | **Sonnet** (not Haiku: the compositions are what the user cares about; weak layouts cost more fix rounds than they save) | "written s_x..s_y, anchors clean" |
| 5 Visual review of contact sheets | Yes | **Sonnet** (Haiku is too weak at spotting visual problems; even the main model missed the off-centre "4") | one line per problem: scene, what, suggested fix |
| 7 Report | No | - | - |

Points to settle with the user:
- **Brief design.** Each subagent starts cold and reads the cheat sheet (~5-8k tokens), so only big chunks
  are worth splitting off. Each brief needs: the files to read (SKILL.md sections by name, not the whole
  file), the exact output format, and what NOT to read (kit source, the reference video).
- **Scene-writing split.** Main writes the storyboard as a spec (scene id, window, concept, information shape,
  anchors, assets, face on/off); subagents write their chunk into separate files (e.g. `scenes_part2.py`)
  that scenes.py imports, so they don't conflict. They need the `A`/`at`/`K` helpers and the shared helpers.
  Uniqueness across chunks ("no two consecutive scenes share a layout") has to be in the spec.
- **Reviewer.** Give it the list of contact sheets, the storyboard (so it can judge "wrong text" / "ring on
  the wrong thing"), and the checklist from SKILL.md step 5. Main applies the fixes, or the subagent does, as
  one Python replace script.
- **Where it lives.** Add the briefs to SKILL.md as explicit steps ("step 3: spawn a Haiku subagent with
  this brief"), or as separate files under the skill folder that the steps point to (fewer tokens when not
  used).
- **Measure.** Compare the next video's total tokens with this run (250k+). The `usage-analysis` skill reads
  local transcripts and can give the numbers per session.

## Paths

- Skill: `.claude/skills/excalidraw-broll/` (SKILL.md, `kit/`, `reference/`)
- Kit: `kit/scripts/{tools.py, doodles.py, blocks.py, sketch.py, build.py}`, `kit/scripts/plumbing/{shoot.py,
  layout.py, fileshot.py, qc.py, assemble.py}`, `kit/{setup.sh, snap.sh, render.sh, progress.py}`
- Last run: `~/Videos/YT Projects/13 everyday tasks your agent does better than you/broll/` (scenes.py,
  HANDOFF.md, research/snap*/contact sheets)
- User rules that matter here: confirm before starting render.sh (CPU may be busy); test render-path changes
  with a short `--test` clip; never add Claude attribution to commits.

# B-roll templates

Reusable HyperFrames projects for YouTube B-roll. Each folder is one template; its
`index.html` declares the variables it accepts (`data-composition-variables` on `<html>`).
Everything renders at 1920x1080, 30 fps.

## Templates

| Template    | Length  | Variables | Use |
| ----------- | ------- | --------- | --- |
| `step-card` | elastic (default 3.2 s) | `number`, `total`, `label`, `title`, `theme`, `color-ground`, `color-ink`, `color-muted`, `color-accent`, `font`, `font-label` | Numbered point card: "Step 2 of 4", "Red flag #3", "Automation Tip #7", "#1 of 15" |
| `big-stat`  | elastic (default 3.5 s) | `value`, `label`, `kicker`, `source`, `count`, `layout`, `theme`, `color-ground`, `color-ink`, `color-muted`, `color-accent`, `font`, `font-label` | One big number: "70%", "$3.4B", "19,000", "1.3 billion", "200 req/hour" |
| `terminal`  | elastic (default 5 s) | `command`, `prompt`, `output`, `flag`, `highlight`, `title`, `chrome`, `typing-speed`, `beats`, `theme`, `color-ground`, `color-window`, `color-ink`, `color-muted`, `color-prompt`, `color-accent`, `font`, `font-label` | A command typed into a terminal: `rm -rf`, `sudo`, `curl … \| bash`, `--dangerously-skip-permissions` |
| `flow`      | elastic (default 6 s) | `steps`, `notes`, `title`, `loop`, `loop-label`, `numbered`, `layout`, `beats`, `theme`, `color-ground`, `color-node`, `color-ink`, `color-muted`, `color-accent`, `font`, `font-label` | 2–6 boxes joined by arrows that light up in turn: the agent loop, user → app → vector DB → LLM, record → fix → process → publish |
| `checklist` | elastic (default 5 s) | `items`, `title`, `kicker`, `mark`, `reveal`, `strike`, `beats`, `theme`, `color-ground`, `color-ink`, `color-muted`, `color-check`, `color-accent`, `font`, `font-label` | Items that get a tick or a cross: "Don't automate: …", "Review for architecture / performance / security", command red flags |

### `step-card`

- `label` is a pattern: `{n}` is replaced by `number`, `{total}` by `total`.
  Default `"Step {n} of {total}"`. Examples: `"Step {n}/{total}"`, `"Red flag #{n}"`, `"#{n} of {total}"`.
- `title` auto-fits: 112px down to 56px, up to 3 lines (max 80 characters).
- `theme`: `dark` (default) or `light`. A colour variable left at its default follows the theme;
  set it to anything else to override it in either theme.

  | Variable | Dark | Light |
  | --- | --- | --- |
  | `color-ground` (background) | `#050608` | `#f5f4f0` |
  | `color-ink` (title) | `#f2f4f7` | `#0c0e12` |
  | `color-muted` (label) | `#8b929c` | `#6a707a` |
  | `color-accent` (number) | `#ff2d3d` | `#e11d2e` |

- `font` (number and title): Inter (default), Montserrat, Outfit, Poppins, Nunito, Roboto,
  Oswald, League Gothic, Archivo Black, Playfair Display, EB Garamond, JetBrains Mono, Space Mono.
- `font-label`: IBM Plex Mono (default), JetBrains Mono, Space Mono, Source Code Pro, Inter,
  Montserrat, Outfit, Poppins, Oswald.

### `big-stat`

- `value` is written the way it should read: `"70%"`, `"$3.4B"`, `"19,000"`, `"1.3 billion"`,
  `"200 req/hour"`, `"5x"`. The first run of digits counts up like an odometer, keeping its
  commas and decimals. Short symbols around it (`$`, `%`, `B`, `x`, `+`) stay full size;
  words (`billion`, `req/hour`) are drawn small on the same baseline.
- `count`: `true` (default) counts up over ~1.3 s; `false` just lands the number (years, version numbers).
- `label` (under the number) auto-fits 88px down to 52px, up to 2 lines. `kicker` (small caps
  line above) and `source` (bottom line, e.g. `"Source: Stack Overflow survey 2025"`) are optional.
- `layout`: `center` (default) or `left`.
- `theme`, colours and fonts work as in `step-card`: `color-accent` is the number,
  `color-ink` the label, `color-muted` the kicker and source; `font` is the number and label,
  `font-label` the kicker and source. Same font options as `step-card`.
- Motion: number rolls up with a motion blur, label lands grey and reads itself into ink word
  by word, then a slow push-in; last 6 frames still.

### `terminal`

- `command` is typed with a human (but deterministic) rhythm at `typing-speed` characters per
  second (default 24), squeezed to at most 45% of the clip. It wraps only at spaces.
- `prompt`: default `$`; anything up to 24 characters, e.g. `"~/app $"`, `"root@server:~#"`.
- `output`: one output line per newline (`\n` in JSON; a literal `\n` also works with `--var`).
  Start a line with `[red]`, `[green]` or `[dim]` to colour it. Up to ~6 lines fit.
- `beats`: give the clip `"beats"` (phrases or seconds) and each output line prints on its
  beat; without it the lines print 0.16 s apart right after the command is entered. A fresh
  prompt with a blinking caret follows the output.
- `flag` (optional, empty by default): a warning badge that lands on the window's bottom edge
  when the command is entered; the window border turns `color-accent` with it. Leave it out for
  normal commands.
- `highlight` (optional, empty by default): a part of the command (exact text) that turns
  `color-accent` when the command is entered, e.g. `"-rf"`, `"| bash"`.
- `chrome`: `dots` (title bar with three dots and `title`, default `bash`) or `plain` (no bar).
- `theme`: dark (default) or light.

  | Variable | Dark | Light |
  | --- | --- | --- |
  | `color-ground` (background) | `#050608` | `#f5f4f0` |
  | `color-window` | `#0f1115` | `#ffffff` |
  | `color-ink` (command) | `#f2f4f7` | `#0c0e12` |
  | `color-muted` (output, title) | `#9aa1ab` | `#5f656e` |
  | `color-prompt` (prompt, `[green]`) | `#3fd17a` | `#0e8a4a` |
  | `color-accent` (flag, highlight, `[red]`) | `#ff2d3d` | `#e11d2e` |

- `font`: JetBrains Mono (default), IBM Plex Mono, Source Code Pro, Space Mono.
  `font-label` (flag and title): same options as `step-card`'s label font.
- Unlike the other templates, the window is already on screen at frame 0 (an empty prompt).

### `flow`

- `steps`: the box labels separated by `|` (2–6; extra ones are dropped). If a label itself
  contains `|`, separate the steps with newlines instead (`\n` in JSON).
- `notes` (optional): a small line under each label, same order and separator; leave a slot
  empty to skip one (`"asks||finds chunks|answers"`).
- `beats`: give the clip one beat per step and each step lights up on it, with the arrow
  drawing into it. With `loop` on, one extra beat times the loop-back. Without beats the steps
  light up evenly through the first ~70% of the clip.
- Frame 0 already shows the whole diagram, dim. The current step gets the `color-accent`
  border and tint; steps already passed keep ink labels on a quieter border.
- `loop` (default off): an arrow from the last step back to the first, which lights the first
  step again. `loop-label` (optional) sits on that arrow, e.g. `"repeat"`.
- `numbered` (default off): "01", "02", … above each label.
- `layout`: `auto` (default) uses a row unless the labels would drop under 48px (long
  labels, 6 steps), then a column. `row` and `column` force one.
- `title` (optional): small caps line above the diagram.
- `theme`: dark (default) or light; `color-node` is the box fill (`#0f1115` dark, `#ffffff`
  light). Other colours as in `step-card`: `color-ink` lit labels, `color-muted` dim labels,
  notes and title, `color-accent` the current step and arrows. `font` (labels) and
  `font-label` (title, numbers, notes) have the same options as `step-card`.

### `checklist`

- `items`: separated by `|` (1–6), or by newlines if an item contains `|`. Each item fits in
  up to 2 lines. Text in `` `backticks` `` is set in the `font-label` font on a code chip
  (`` "`rm -rf`|`sudo` when you didn't expect it" ``).
- `mark`: `check` (default, a tick in `color-check`) or `cross` (a cross in `color-accent`)
  for every item. Start an item with `[do]` or `[dont]` to give it a tick or a cross in a
  mixed list: `"[do]Read the diff|[dont]Merge blind"`.
- `beats`: one per item; each item's mark lands on its beat. Without beats they land evenly
  through the first ~60% of the clip.
- `reveal`: `each` (default) slides each item in on its beat; `all` shows the whole list dim
  from frame 0 and only the marks land on the beats.
- `strike` (default off): crossed items get a red line through them.
- `title` (default `"Review every AI change for"`; set `""` for none) auto-fits 128px on one
  line, down to 72px on two. `kicker` (optional) is a small caps line above it. Items auto-fit
  up to 88px (smaller than the title), down to 44px.
- `theme`: dark (default) or light; `color-check` is `#3fd17a` dark, `#0e8a4a` light. Other
  colours and fonts as in `step-card` (`color-muted` is the kicker, empty boxes and items
  that haven't landed yet).

## Rendering with `phantom broll`

`broll.py` renders clips timed to a video's word-level captions, from a cue file or flags.
Full docs: [docs/broll.md](../docs/broll.md).

```bash
phantom broll list big-stat                           # variables
phantom broll render broll/cues.json --dry-run        # check timing
phantom broll render broll/cues.json                  # render (unchanged clips are skipped)
phantom broll clip step-card -o step-1.mp4 --cue "So number one" --until "different sites" --var number=1
```

## Length

Variables can't change a composition's length, so the length is the `data-duration` on the
root element in `index.html`. `broll.py` renders a temp copy with that attribute (and any
element marked `data-broll-span`) set to the clip's length. Templates must read their length
at runtime and time nothing else, so the animation stretches to fit. `step-card`: a fixed
~1.2 s entrance, then a slow push-in hold, and the last 6 frames stay still. Looks right from
2 s to 20 s.

## Rendering by hand

From inside a template folder (default length):

```bash
npx hyperframes render -o out.mp4 --strict-variables \
  --variables '{"number":1,"total":4,"title":"Change weak & reused passwords","theme":"light"}'
```

Every template is opaque and has no exit animation, so it can be hard-cut in and out of the
talking head.

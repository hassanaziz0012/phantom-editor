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

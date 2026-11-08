# B-roll Templates

Reusable HyperFrames B-roll clips (numbered cards, big stats, terminal commands, flow diagrams, checklists, and more to come), rendered at exactly the length of the speech they cover. Each template is authored once; per video you only write a small cue file and run one command, which costs no LLM tokens.

## Architecture

- `broll_templates/<name>/` is one HyperFrames project per template. Its `index.html` declares the variables it accepts (`data-composition-variables`). See [broll_templates/README.md](../broll_templates/README.md) for each template's variables.
- Variables can't change a composition's length, so the wrapper copies the template to a temp folder, sets `data-duration` on the root (and on any element marked `data-broll-span`), and renders the copy with `npx hyperframes render --variables-file … --strict-variables`. Templates read their length at runtime, so the animation stretches to fit.
- Timing comes from the project's word-level captions (`trimmed-1word.srt`, found next to the cue file or one or two folders up). Phrases are fuzzy-matched (hyphens, punctuation and case are ignored, small typos are fine).

## Scripts

### [broll.py](../broll_templates/broll.py)

* **CLI Command**:
  ```bash
  # Templates and their variables (types, defaults, enum options):
  phantom broll list
  phantom broll list step-card

  # Check where a phrase is said before using it as a cue:
  phantom broll find "two factor authentication" --srt trimmed-1word.srt

  # Resolve every clip's timing without rendering:
  phantom broll render broll/cues.json --dry-run

  # Render every clip in a cue file (unchanged clips are skipped):
  phantom broll render broll/cues.json
  phantom broll render broll/cues.json --only step-2.mp4 --force

  # One clip from flags:
  phantom broll clip step-card -o broll/step-1.mp4 --srt trimmed-1word.srt \
    --cue "So number one" --until "on all these different sites" \
    --var number=1 --var total=4 --var "title=Change weak & reused passwords"
  ```

## Cue file

A list of clips, or an object with shared `defaults` and an optional `srt` path. Paths are relative to the cue file.

```json
{
  "defaults": {"template": "step-card", "variables": {"total": 4, "theme": "light"}},
  "clips": [
    {"file": "step-1.mp4", "cue": "So number one", "until": "on all these different sites",
     "variables": {"number": 1, "title": "Change weak & reused passwords"}},
    {"file": "step-2.mp4", "cue": "set up two-factor authentication", "until": "software that you use",
     "variables": {"number": 2, "title": "Turn on 2FA everywhere"}}
  ]
}
```

| Key | Meaning |
| --- | --- |
| `template` | Folder name in `broll_templates/` |
| `file` | Output MP4 |
| `variables` | Template variables, checked against the template's declared types before rendering |
| `cue` / `at` | Start: the first word of the `cue` phrase, or a timestamp (`101.7`, `1:41.68`, `00:01:41,680`) |
| `until` / `duration` | End: the last word of the `until` phrase (searched up to 90 s after the start), or a length in seconds. Default: the template's own length |
| `after` | The cue must be said after this timestamp (for phrases said more than once) |
| `offset` | Seconds added to the start (negative starts earlier) |
| `beats` | Phrases or seconds, for templates with a `beats` variable; sent as seconds from the clip start |

Templates: `step-card` (numbered point), `big-stat` (a number that counts up, with a label),
`terminal` (a command typed into a terminal, with optional output and warning flag), `flow`
(2–6 boxes joined by arrows that light up in turn, optional loop-back) and `checklist` (items
that get a tick or a cross on their beats). Their
variables are in [broll_templates/README.md](../broll_templates/README.md), or run
`phantom broll list <template>`. Mixing templates in one cue file:

```json
{
  "clips": [
    {"template": "big-stat", "file": "uber-budget.mp4", "cue": "three point four billion", "until": "in four months",
     "variables": {"value": "$3.4B", "label": "AI budget, gone in four months", "kicker": "Uber"}},
    {"template": "terminal", "file": "skip-perms.mp4", "cue": "dangerously skip permissions", "until": "no safety net",
     "beats": ["starting the agent", "permission checks"],
     "variables": {"command": "claude --dangerously-skip-permissions", "flag": "No safety net",
                   "highlight": "--dangerously-skip-permissions",
                   "output": "[dim]Starting agent...\n[red]Permission checks disabled"}},
    {"template": "flow", "file": "agent-loop.mp4", "cue": "the agent loop", "until": "and then it repeats",
     "beats": ["it thinks", "it acts", "it observes", "repeats"],
     "variables": {"steps": "Think|Act|Observe", "loop": true, "title": "The agent loop"}},
    {"template": "checklist", "file": "dont-automate.mp4", "cue": "what not to automate", "until": "creative work",
     "beats": ["one-off", "sensitive", "creative work"],
     "variables": {"title": "Don't automate", "items": "One-off tasks|Sensitive tasks|Creative work", "mark": "cross"}}
  ]
}
```

## Output

Renders go to each clip's `file`. Next to the cue file, `<cue file name>.resolved.json` lists every clip's placement in the final video, sorted by time, ready for an overlay step:

```json
{"file": "/…/broll/step-1.mp4", "template": "step-card", "at": 101.667, "at_ts": "1:41.67",
 "duration": 7.667, "end": 109.333, "variables": {…}, "matched": {…}, "hash": "…"}
```

The `hash` covers the template files, variables, length and quality, so a re-run only renders clips that changed (`--force` renders them all).

"""
B-roll renderer for the HyperFrames templates in this folder.

Renders reusable B-roll clips from a cue file (or one clip from flags) at exactly the
length of the speech they cover. Timing comes from the project's word-level captions
(trimmed-1word.srt): a `cue` phrase sets the start, an `until` phrase sets the end, and
optional `beats` phrases become beat times relative to the clip start.

Variables can't change a HyperFrames composition's length, so each clip is rendered from a
temp copy of its template with the root's data-duration patched (plus any element marked
data-broll-span). Templates read their length at runtime, so nothing else needs changing.

Cue file (a list of clips, or {"srt": ..., "defaults": {...}, "clips": [...]}):
    [
      {"template": "step-card", "file": "step-1.mp4", "cue": "So number one", "until": "repeat passwords",
       "variables": {"number": 1, "total": 4, "title": "Change weak & reused passwords"}}
    ]

Per-clip keys: template, file, variables, and timing from either
    cue (phrase) | at (timestamp)       -> start
    until (phrase) | duration (seconds) -> end (default: the template's own length)
plus optional offset (seconds added to the start), after (timestamp the cue must come after)
and beats (phrases or seconds, for templates that declare a `beats` variable).

Writes <cue file stem>.resolved.json next to the cue file with each clip's at / duration /
file, and skips clips whose template, variables and timing haven't changed since the last run.

Usage:
    phantom broll list [template]
    phantom broll find "two-factor authentication" --srt trimmed-1word.srt
    phantom broll render broll/cues.json [--dry-run] [--only step-1.mp4] [--force]
    phantom broll clip step-card -o step-1.mp4 --srt trimmed-1word.srt \\
        --cue "So number one" --until "repeat passwords" --var number=1 --var "title=Change weak passwords"
"""

import argparse
import hashlib
import json
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from rapidfuzz import fuzz

from video_editing.utils import parse_srt, parse_timestamp, print_error, print_info, print_success, print_warning

TEMPLATES_DIR = Path(__file__).resolve().parent
FPS = 30
MIN_DURATION = 1.0
LONG_DURATION = 30.0
MIN_SCORE = 75.0
UNTIL_MAX_SPAN = 90.0
SRT_NAMES = ("trimmed-1word.srt",)
COPY_IGNORE = shutil.ignore_patterns(".hyperframes", "renders", "node_modules")


# ---------------------------------------------------------------- templates


@dataclass
class Template:
    name: str
    path: Path
    variables: list[dict]
    default_duration: float
    hyperframes_version: str | None

    @property
    def var_ids(self) -> dict[str, dict]:
        return {v["id"]: v for v in self.variables}


def available_templates() -> list[str]:
    return sorted(p.parent.name for p in TEMPLATES_DIR.glob("*/index.html"))


def load_template(name: str) -> Template:
    path = TEMPLATES_DIR / name
    index = path / "index.html"
    if not index.is_file():
        raise SystemExit(f"Unknown template '{name}'. Available: {', '.join(available_templates()) or 'none'}")
    html = index.read_text(encoding="utf-8")

    m = re.search(r"data-composition-variables='(.*?)'", html, re.S)
    variables = json.loads(m.group(1).replace("&#39;", "'")) if m else []

    root = re.search(r"<[^>]*\bdata-composition-id=[^>]*>", html)
    dur = re.search(r'\bdata-duration="([\d.]+)"', root.group(0)) if root else None
    if not dur:
        raise SystemExit(f"{index}: no root element with data-composition-id and data-duration")

    version = None
    pkg = path / "package.json"
    if pkg.is_file():
        scripts = json.loads(pkg.read_text()).get("scripts", {})
        vm = re.search(r"hyperframes@([\w.\-]+)", " ".join(scripts.values()))
        version = vm.group(1) if vm else None

    return Template(name, path, variables, float(dur.group(1)), version)


def template_hash(t: Template) -> str:
    h = hashlib.sha256()
    for f in sorted(t.path.rglob("*")):
        rel = f.relative_to(t.path)
        if f.is_file() and rel.parts[0] not in (".hyperframes", "renders", "node_modules"):
            h.update(str(rel).encode())
            h.update(f.read_bytes())
    return h.hexdigest()


def coerce_variable(spec: dict, value):
    """Converts a value to the variable's declared type, or raises ValueError."""
    kind = spec.get("type")
    vid = spec["id"]
    if kind == "number":
        try:
            num = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"'{vid}' must be a number, got {value!r}")
        if "min" in spec and num < spec["min"] or "max" in spec and num > spec["max"]:
            raise ValueError(f"'{vid}' must be between {spec.get('min')} and {spec.get('max')}, got {value!r}")
        return int(num) if num.is_integer() else num
    if kind == "boolean":
        if isinstance(value, bool):
            return value
        if str(value).lower() in ("true", "1", "yes", "on"):
            return True
        if str(value).lower() in ("false", "0", "no", "off"):
            return False
        raise ValueError(f"'{vid}' must be true or false, got {value!r}")
    if kind == "enum":
        options = [o["value"] if isinstance(o, dict) else o for o in spec.get("options", [])]
        if value not in options:
            raise ValueError(f"'{vid}' must be one of {options}, got {value!r}")
        return value
    if kind == "color":
        if not re.fullmatch(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})", str(value)):
            raise ValueError(f"'{vid}' must be a hex colour like #ff2d3d, got {value!r}")
        return str(value)
    value = str(value)
    if spec.get("maxLength") and len(value) > spec["maxLength"]:
        raise ValueError(f"'{vid}' is {len(value)} characters, max is {spec['maxLength']}: {value!r}")
    return value


def validate_variables(t: Template, variables: dict) -> dict:
    specs = t.var_ids
    out = {}
    for key, value in variables.items():
        if key not in specs:
            raise ValueError(f"'{key}' is not a variable of {t.name}. Variables: {', '.join(specs)}")
        out[key] = coerce_variable(specs[key], value)
    return out


# ---------------------------------------------------------------- captions


@dataclass
class Word:
    token: str
    start: float
    end: float
    text: str


def normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9%$]", "", text.lower())


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens; hyphenated words split ("two-factor" -> "two", "factor")."""
    return [t for t in (normalize(p) for p in re.split(r"[\s\-\u2013\u2014/]+", text)) if t]


def load_words(srt_path: Path) -> list[Word]:
    words = []
    for start, end, text in parse_srt(srt_path):
        for raw in text.split():
            for tok in tokenize(raw):
                words.append(Word(tok, start, end, raw))
    if not words:
        raise SystemExit(f"No words found in {srt_path}")
    return words


@dataclass
class Match:
    first: int
    last: int
    score: float
    start: float
    end: float
    text: str


def find_matches(words: list[Word], phrase: str, after: float = 0.0, before: float | None = None) -> list[Match]:
    """Scores every word window against the phrase; best match per start word, best first."""
    tokens = tokenize(phrase)
    if not tokens:
        raise ValueError(f"Phrase has no words: {phrase!r}")
    target = " ".join(tokens)
    n = len(tokens)
    sizes = sorted({max(1, n - 1), n, n + 1})

    best: dict[int, Match] = {}
    for i, w in enumerate(words):
        if w.start < after - 1e-6:
            continue
        if before is not None and w.start > before:
            break
        for size in sizes:
            j = i + size
            if j > len(words):
                continue
            score = fuzz.ratio(target, " ".join(x.token for x in words[i:j]))
            if i not in best or score > best[i].score:
                text = " ".join(dict.fromkeys(x.text for x in words[i:j]))  # a split word shows once
                best[i] = Match(i, j - 1, score, w.start, words[j - 1].end, text)
    return sorted(best.values(), key=lambda m: (-m.score, m.start))


def distinct(matches: list[Match], min_score: float) -> list[Match]:
    """Strong matches that don't overlap a better one."""
    kept: list[Match] = []
    for m in matches:
        if m.score < min_score:
            break
        if all(m.last < k.first or m.first > k.last for k in kept):
            kept.append(m)
    return kept


def pick(matches: list[Match], phrase: str, min_score: float, what: str) -> tuple[Match, list[Match]]:
    """Earliest match within 5 points of the best one; also returns the other strong matches."""
    strong = distinct(matches, min_score)
    if not strong:
        hint = f" Closest: {matches[0].text!r} at {fmt_ts(matches[0].start)} (score {matches[0].score:.0f})." if matches else ""
        raise ValueError(f"No {what} match for {phrase!r}.{hint}")
    top = strong[0].score
    near = [m for m in strong if m.score >= top - 5]
    chosen = min(near, key=lambda m: m.start)
    others = [m for m in strong if m is not chosen and m.score >= max(90, top - 5)]
    return chosen, others


def fmt_ts(seconds: float) -> str:
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}" if h >= 1 else f"{int(m)}:{s:05.2f}"


def find_srt(start_dir: Path) -> Path | None:
    for d in (start_dir, *start_dir.parents[:2]):
        for name in SRT_NAMES:
            if (d / name).is_file():
                return d / name
    return None


# ---------------------------------------------------------------- clips


@dataclass
class Clip:
    template: Template
    output: Path
    variables: dict
    at: float = 0.0
    duration: float = 0.0
    beats: list[float] = field(default_factory=list)
    matched: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def render_variables(self) -> dict:
        out = dict(self.variables)
        specs = self.template.var_ids
        if "duration" in specs:
            out["duration"] = round(self.duration, 3)
        if self.beats:
            out["beats"] = ",".join(f"{b:.3f}" for b in self.beats)
        return out

    def fingerprint(self, quality: str) -> str:
        payload = json.dumps(
            [template_hash(self.template), self.render_variables(), round(self.duration, 3), quality], sort_keys=True
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


def resolve_clip(entry: dict, base_dir: Path, words: list[Word] | None, min_score: float) -> Clip:
    name = entry.get("template")
    if not name:
        raise ValueError("missing 'template'")
    if not entry.get("file"):
        raise ValueError("missing 'file'")
    t = load_template(name)
    output = Path(entry["file"]).expanduser()
    output = output if output.is_absolute() else base_dir / output
    clip = Clip(t, output.resolve(), validate_variables(t, entry.get("variables", {})))

    def need_words(key: str) -> list[Word]:
        if words is None:
            raise ValueError(f"'{key}' needs a word-level SRT: pass --srt or put trimmed-1word.srt next to the cue file")
        return words

    # Start
    if entry.get("at") is not None:
        start = parse_timestamp(str(entry["at"]))
        start_index = None
    elif entry.get("cue"):
        after = parse_timestamp(str(entry["after"])) if entry.get("after") is not None else 0.0
        w = need_words("cue")
        m, others = pick(find_matches(w, entry["cue"], after=after), entry["cue"], min_score, "cue")
        start, start_index = m.start, m.first
        clip.matched["cue"] = {"text": m.text, "score": round(m.score, 1), "at": round(m.start, 3)}
        if others:
            where = ", ".join(f"{fmt_ts(o.start)} ({o.text!r})" for o in others[:3])
            clip.warnings.append(f"cue also matches at {where}; add \"after\" or a longer cue if the wrong one was picked")
    else:
        raise ValueError("needs 'cue' or 'at' for its start")
    start = max(0.0, start + float(entry.get("offset", 0)))

    # End
    if entry.get("duration") is not None:
        end = start + float(entry["duration"])
    elif entry.get("until"):
        w = need_words("until")
        matches = find_matches(w, entry["until"], after=start, before=start + UNTIL_MAX_SPAN)
        m, _ = pick(matches, entry["until"], min_score, "until")
        end = m.end
        clip.matched["until"] = {"text": m.text, "score": round(m.score, 1), "end": round(m.end, 3)}
    else:
        end = start + t.default_duration

    # Whole frames
    clip.at = round(start * FPS) / FPS
    clip.duration = round((end - start) * FPS) / FPS
    if clip.duration < MIN_DURATION:
        raise ValueError(f"resolves to {clip.duration:.2f} s, shorter than {MIN_DURATION} s (check cue/until)")
    if clip.duration > LONG_DURATION:
        clip.warnings.append(f"{clip.duration:.1f} s is long for B-roll; consider cutting back to the face sooner")

    # Beats, relative to the clip start, in order
    beats = entry.get("beats") or []
    if beats:
        if "beats" not in t.var_ids:
            raise ValueError(f"'beats' given but {t.name} has no beats variable")
        cursor = start
        for b in beats:
            if isinstance(b, (int, float)) or re.fullmatch(r"\s*\d+(\.\d+)?\s*", str(b)):
                rel = float(b)
            else:
                m, _ = pick(find_matches(need_words("beats"), b, after=cursor, before=end), b, min_score, "beat")
                rel = m.start - start
                cursor = m.start
            clip.beats.append(round(max(0.0, rel), 3))
    return clip


def patch_duration(html: str, duration: float) -> str:
    """Sets data-duration on the composition root and on every data-broll-span element."""
    # Truncated, not rounded: HyperFrames rounds partial frames up, so 7.667 s would be 231 frames
    value = f"{int(duration * 1000 + 1e-6) / 1000:.3f}".rstrip("0").rstrip(".")

    def patch(tag: re.Match) -> str:
        return re.sub(r'\bdata-duration="[^"]*"', f'data-duration="{value}"', tag.group(0))

    html, n = re.subn(r"<[^>]*\bdata-composition-id=[^>]*>", patch, html, count=1)
    if not n:
        raise ValueError("template has no root element with data-composition-id")
    return re.sub(r"<[^>]*\bdata-broll-span\b[^>]*>", patch, html)


def render_clip(clip: Clip, quality: str) -> None:
    t = clip.template
    clip.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f"broll-{t.name}-") as tmp:
        work = Path(tmp) / t.name
        shutil.copytree(t.path, work, ignore=COPY_IGNORE)
        index = work / "index.html"
        index.write_text(patch_duration(index.read_text(encoding="utf-8"), clip.duration), encoding="utf-8")
        vars_file = Path(tmp) / "variables.json"
        vars_file.write_text(json.dumps(clip.render_variables()), encoding="utf-8")

        pkg = f"hyperframes@{t.hyperframes_version}" if t.hyperframes_version else "hyperframes"
        cmd = [
            "npx", "--yes", pkg, "render", str(work),
            "--variables-file", str(vars_file), "--strict-variables",
            "--quality", quality, "-o", str(clip.output),
        ]
        proc = subprocess.run(cmd, cwd=work, capture_output=True, text=True)
        if proc.returncode != 0 or not clip.output.is_file():
            tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-25:])
            raise RuntimeError(f"hyperframes render failed for {clip.output.name}:\n{tail}")


# ---------------------------------------------------------------- commands


def load_cue_file(path: Path) -> tuple[list[dict], dict, str | None]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data, {}, None
    if isinstance(data, dict) and isinstance(data.get("clips"), list):
        return data["clips"], data.get("defaults", {}), data.get("srt")
    raise SystemExit(f"{path}: expected a list of clips or an object with a \"clips\" list")


def merge_defaults(entry: dict, defaults: dict) -> dict:
    merged = {**defaults, **entry}
    merged["variables"] = {**defaults.get("variables", {}), **entry.get("variables", {})}
    return merged


def resolve_srt(arg: str | None, from_file: str | None, base_dir: Path) -> Path | None:
    for candidate in (arg, from_file):
        if candidate:
            p = Path(candidate).expanduser()
            p = p if p.is_absolute() else (Path.cwd() / p if candidate == arg else base_dir / p)
            if not p.is_file():
                raise SystemExit(f"SRT not found: {p}")
            return p.resolve()
    return find_srt(base_dir)


def print_plan(clips: list[Clip], statuses: list[str]) -> None:
    for clip, status in zip(clips, statuses):
        end = clip.at + clip.duration
        print(f"  {clip.output.name:<28} {clip.template.name:<12} {fmt_ts(clip.at)} -> {fmt_ts(end)}  ({clip.duration:.2f} s)  {status}")
        for key, m in clip.matched.items():
            print(f"      {key:<5} matched {m['text']!r} (score {m['score']:.0f})")
        if clip.beats:
            print(f"      beats {', '.join(f'{b:.2f}' for b in clip.beats)}")
        for w in clip.warnings:
            print_warning(f"      ! {w}")


def run_clips(entries: list[dict], base_dir: Path, srt: Path | None, resolved_path: Path, args) -> int:
    words = load_words(srt) if srt else None
    if srt:
        print_info(f"Captions: {srt}")

    clips: list[Clip] = []
    errors = 0
    for i, entry in enumerate(entries, 1):
        label = entry.get("file") or f"clip {i}"
        if args.only and Path(entry.get("file", "")).name not in args.only:
            continue
        try:
            clips.append(resolve_clip(entry, base_dir, words, args.min_score))
        except (ValueError, SystemExit) as e:
            print_error(f"  {label}: {e}")
            errors += 1
    if errors:
        print_error(f"{errors} clip(s) could not be resolved; nothing rendered.")
        return 1
    if not clips:
        print_warning("No clips to render.")
        return 0

    previous = {}
    if resolved_path.is_file():
        try:
            previous = {c["file"]: c for c in json.loads(resolved_path.read_text()).get("clips", [])}
        except (json.JSONDecodeError, AttributeError):
            previous = {}

    statuses = []
    for clip in clips:
        fp = clip.fingerprint(args.quality)
        unchanged = previous.get(str(clip.output), {}).get("hash") == fp and clip.output.is_file()
        statuses.append("up to date" if unchanged and not args.force else "render")

    print_plan(clips, statuses)
    if args.dry_run:
        return 0

    failed: set[int] = set()
    todo = [c for c, s in zip(clips, statuses) if s == "render"]
    for n, clip in enumerate(todo, 1):
        print_info(f"Rendering {n}/{len(todo)}: {clip.output.name} ({clip.duration:.2f} s)...")
        t0 = time.time()
        try:
            render_clip(clip, args.quality)
            print_success(f"  done in {time.time() - t0:.0f} s -> {clip.output}")
        except RuntimeError as e:
            print_error(str(e))
            failed.add(id(clip))

    # Placement info: merge with what was there so --only runs keep the other clips
    merged = dict(previous)
    for clip in clips:
        if id(clip) in failed:
            continue
        merged[str(clip.output)] = {
            "file": str(clip.output),
            "template": clip.template.name,
            "at": round(clip.at, 3),
            "at_ts": fmt_ts(clip.at),
            "duration": round(clip.duration, 3),
            "end": round(clip.at + clip.duration, 3),
            "variables": clip.render_variables(),
            "matched": clip.matched,
            "hash": clip.fingerprint(args.quality),
        }
    ordered = sorted(merged.values(), key=lambda c: c["at"])
    resolved_path.write_text(json.dumps({"srt": str(srt) if srt else None, "clips": ordered}, indent=2) + "\n")
    print_info(f"Placements: {resolved_path}")
    if failed:
        print_error(f"{len(failed)} clip(s) failed to render.")
        return 1
    return 0


def cmd_render(args) -> int:
    cue_file = Path(args.cue_file).expanduser().resolve()
    if not cue_file.is_file():
        raise SystemExit(f"Cue file not found: {cue_file}")
    entries, defaults, srt_from_file = load_cue_file(cue_file)
    base_dir = cue_file.parent
    entries = [merge_defaults(e, defaults) for e in entries]
    srt = resolve_srt(args.srt, srt_from_file, base_dir)
    resolved = cue_file.with_name(f"{cue_file.stem}.resolved.json")
    return run_clips(entries, base_dir, srt, resolved, args)


def cmd_clip(args) -> int:
    variables = {}
    if args.vars_json:
        variables.update(json.loads(args.vars_json))
    for kv in args.var or []:
        if "=" not in kv:
            raise SystemExit(f"--var expects key=value, got {kv!r}")
        k, v = kv.split("=", 1)
        variables[k] = v
    entry = {"template": args.template, "file": args.output, "variables": variables}
    for key in ("cue", "until", "at", "after", "duration", "offset"):
        if getattr(args, key) is not None:
            entry[key] = getattr(args, key)
    if args.beats:
        entry["beats"] = [b.strip() for b in args.beats.split("|") if b.strip()]
    output = Path(args.output).expanduser().resolve()
    srt = resolve_srt(args.srt, None, output.parent)
    args.only = None
    resolved = output.with_name(f"{output.stem}.resolved.json")
    return run_clips([entry], Path.cwd(), srt, resolved, args)


def cmd_find(args) -> int:
    base = Path.cwd()
    srt = resolve_srt(args.srt, None, base)
    if not srt:
        raise SystemExit("No captions found: pass --srt <trimmed-1word.srt>")
    words = load_words(srt)
    after = parse_timestamp(args.after) if args.after else 0.0
    matches = distinct(find_matches(words, args.phrase, after=after), 0)[: args.limit]
    for m in matches:
        print(f"  {fmt_ts(m.start):>9}  score {m.score:>3.0f}  {m.text}")
    return 0


def cmd_list(args) -> int:
    names = [args.template] if args.template else available_templates()
    for name in names:
        t = load_template(name)
        print(f"{t.name}  (default length {t.default_duration:g} s; elastic)")
        for v in t.variables:
            extra = ""
            if v.get("type") == "enum":
                extra = " one of: " + ", ".join(o["value"] if isinstance(o, dict) else o for o in v.get("options", []))
            elif v.get("type") == "number" and ("min" in v or "max" in v):
                extra = f" {v.get('min', '')}..{v.get('max', '')}"
            elif v.get("maxLength"):
                extra = f" max {v['maxLength']} chars"
            print(f"  {v['id']:<14} {v.get('type', ''):<7} default {json.dumps(v.get('default'))}{extra}")
            if v.get("label"):
                print(f"  {'':<14} {v['label']}")
        print()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Render HyperFrames B-roll templates timed to a video's captions.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("list", help="List templates and their variables")
    p.add_argument("template", nargs="?")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("find", help="Search the word-level captions for a phrase")
    p.add_argument("phrase")
    p.add_argument("--srt", help="Word-level SRT (default: trimmed-1word.srt in the current folder)")
    p.add_argument("--after", help="Only look after this timestamp")
    p.add_argument("--limit", type=int, default=5)
    p.set_defaults(func=cmd_find)

    def add_render_flags(p):
        p.add_argument("--srt", help="Word-level SRT (default: trimmed-1word.srt next to the output, or one level up)")
        p.add_argument("--dry-run", action="store_true", help="Resolve timing and print the plan without rendering")
        p.add_argument("--force", action="store_true", help="Re-render clips that are up to date")
        p.add_argument("--quality", default="looks", choices=["draft", "looks", "delivery", "standard", "high"])
        p.add_argument("--min-score", type=float, default=MIN_SCORE, help=f"Fuzzy match threshold 0-100 (default {MIN_SCORE:g})")

    p = sub.add_parser("render", help="Render every clip in a cue file")
    p.add_argument("cue_file")
    p.add_argument("--only", nargs="+", metavar="FILE", help="Only these output file names")
    add_render_flags(p)
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("clip", help="Render one clip from flags")
    p.add_argument("template")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--cue", help="Phrase where the clip starts")
    p.add_argument("--until", help="Phrase where the clip ends (end of its last word)")
    p.add_argument("--at", help="Start timestamp instead of --cue")
    p.add_argument("--after", help="The cue must come after this timestamp")
    p.add_argument("--duration", type=float, help="Length in seconds instead of --until")
    p.add_argument("--offset", type=float, help="Seconds added to the start (negative is earlier)")
    p.add_argument("--beats", help="Beat phrases or seconds separated by |")
    p.add_argument("--var", action="append", metavar="KEY=VALUE", help="Template variable (repeatable)")
    p.add_argument("--vars-json", help="Template variables as a JSON object")
    add_render_flags(p)
    p.set_defaults(func=cmd_clip)

    args = parser.parse_args()
    sys.stdout.reconfigure(line_buffering=True)  # live progress when piped (agents, logs)
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)  # quiet exit when piped into head
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

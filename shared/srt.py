"""Reading SRT subtitle files."""

import re
from pathlib import Path

from shared.timecodes import format_timestamp, parse_timestamp


def parse_srt(srt_path: str | Path) -> list[tuple[float, float, str]]:
    """
    Parses an SRT subtitle file into a list of (start_seconds, end_seconds, text) tuples.
    """
    path = Path(srt_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"SRT captions file not found: {path}")

    content = path.read_text(encoding="utf-8", errors="replace")
    content = content.replace("\r\n", "\n").strip()
    blocks = re.split(r"\n\s*\n", content)

    timestamp_pattern = re.compile(
        r"(\d{1,2}:\d{2}:\d{2}(?:[,\.]\d{1,3})?)\s*-->\s*(\d{1,2}:\d{2}:\d{2}(?:[,\.]\d{1,3})?)"
    )

    captions: list[tuple[float, float, str]] = []
    for block in blocks:
        lines = block.strip().split("\n")
        if len(lines) < 2:
            continue

        time_line = lines[1] if len(lines) >= 2 and ("-->" in lines[1]) else lines[0]
        match = timestamp_pattern.search(time_line)
        if not match:
            continue

        start_t = parse_timestamp(match.group(1))
        end_t = parse_timestamp(match.group(2))

        time_line_idx = lines.index(time_line)
        raw_text = " ".join(lines[time_line_idx + 1 :]).strip()
        cleaned_text = re.sub(r"<[^>]+>", "", raw_text).strip()
        if cleaned_text:
            captions.append((start_t, end_t, cleaned_text))

    return captions


def parse_srt_to_text(srt_path: str | Path) -> str:
    """
    Parses an SRT subtitle file into clean continuous plain text.
    """
    captions = parse_srt(srt_path)
    full_text = " ".join(text for _, _, text in captions)
    return re.sub(r"\s+", " ", full_text).strip()


def parse_srt_to_timestamped_transcript(srt_path: str | Path) -> str:
    """
    Parses an SRT subtitle file into a formatted transcript with [MM:SS] or [HH:MM:SS] cues.
    """
    captions = parse_srt(srt_path)
    if not captions:
        raise ValueError(f"No subtitle segments could be extracted from: {srt_path}")

    return "\n".join(f"[{format_timestamp(int(start_t))}] {text}" for start_t, _, text in captions)

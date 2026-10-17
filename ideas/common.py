"""
Helpers shared by the idea generators (`phantom ideas generate-from-comments`
and `phantom ideas generate-from-outlier`).
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger("phantom.ideas")


class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    ITALIC = "\033[3m"
    CYAN = "\033[36m"
    BRIGHT_CYAN = "\033[96m"
    GREEN = "\033[32m"
    BRIGHT_GREEN = "\033[92m"
    YELLOW = "\033[33m"
    BRIGHT_YELLOW = "\033[93m"
    BLUE = "\033[34m"
    BRIGHT_BLUE = "\033[94m"
    MAGENTA = "\033[35m"
    BRIGHT_MAGENTA = "\033[95m"
    RED = "\033[31m"
    GRAY = "\033[90m"
    WHITE = "\033[97m"


# ── Fetch Recent Channel Video Titles ──────────────────────────────────────────

def fetch_recent_channel_titles(
    channel: Optional[str] = None,
    limit: int = 20,
    api_key: Optional[str] = None,
) -> List[str]:
    """
    Fetches the latest `limit` video titles from the user's YouTube channel
    (YOUTUBE_CHANNEL_ID and YOUTUBE_API_KEY from .env). Returns [] if either is
    missing or the API call fails.
    """
    api_key = api_key or os.getenv("YOUTUBE_API_KEY")
    channel_target = channel or os.getenv("YOUTUBE_CHANNEL_ID")
    if not api_key or not channel_target:
        logger.debug("YOUTUBE_API_KEY or YOUTUBE_CHANNEL_ID not set. Skipping channel titles.")
        return []

    try:
        from youtube_api.fetch_videos import fetch_channel_videos
        from youtube_api.utils import get_youtube_client, resolve_channel_id

        youtube_client = get_youtube_client(api_key)
        resolved_id = resolve_channel_id(youtube_client, channel_target)
        videos = fetch_channel_videos(api_key, resolved_id, quiet=True, youtube=youtube_client)
        return [v.title for v in videos[:limit]]
    except Exception as e:
        logger.warning("Failed to fetch recent channel video titles: %s", e)
        return []


# ── Parse Claude Response ─────────────────────────────────────────────────────

def parse_ideas_response(response_text: str) -> List[Dict[str, Any]]:
    """
    Parses Claude's response into a list of idea dicts ({source, idea} plus
    confidence_score / best_formats when given). Handles raw JSON, markdown code
    fences, or a JSON array embedded in prose.
    """
    text = response_text.strip()
    if not text:
        return []

    candidates = [text]
    # JSON inside markdown code fences ```json ... ``` or ``` ... ```
    candidates += [m.strip() for m in re.findall(r"```(?:json)?\s*([\s\S]*?)\s*```", text)]
    # The outermost JSON array [...]
    first_bracket, last_bracket = text.find("["), text.rfind("]")
    if first_bracket != -1 and last_bracket > first_bracket:
        candidates.append(text[first_bracket : last_bracket + 1])

    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(data, list):
            return _validate_ideas_list(data)

    logger.warning("Could not parse a JSON list of ideas from the response. Returning raw text as a single entry.")
    return [{"source": "BrowserLLM Raw Response", "idea": text}]


def _validate_ideas_list(data: List[Any]) -> List[Dict[str, Any]]:
    """Validates and cleans the list of idea objects."""
    results: List[Dict[str, Any]] = []
    for item in data:
        if isinstance(item, dict):
            source = str(item.get("source", "")).strip()
            idea = str(item.get("idea", "")).strip()
            confidence_score = str(item.get("confidence_score", item.get("confidenceScore", ""))).strip()
            raw_formats = next(
                (item[k] for k in ("best_formats", "best formats", "bestFormats") if item.get(k) is not None),
                None,
            )
            best_formats: Union[List[str], str] = ""
            if isinstance(raw_formats, list):
                best_formats = [str(f).strip() for f in raw_formats if str(f).strip()]
            elif raw_formats is not None and str(raw_formats).strip():
                best_formats = str(raw_formats).strip()

            if source or idea:
                entry: Dict[str, Any] = {"source": source, "idea": idea}
                if confidence_score:
                    entry["confidence_score"] = confidence_score
                if best_formats:
                    entry["best_formats"] = best_formats
                results.append(entry)
        elif isinstance(item, str) and item.strip():
            results.append({"source": "", "idea": item.strip()})
    return results


# ── Export ────────────────────────────────────────────────────────────────────

def export_ideas(
    ideas: List[Dict[str, Any]],
    video_url: str,
    source_type: str,
    spreadsheet_id: Optional[str] = None,
) -> int:
    """
    Appends the ideas to the Content Calendar's 'Ideas' tab, each sourced as
    "<source text> - <video_url>". Returns the number of rows appended.
    """
    from ideas.export_ideas_to_sheet import export_ideas_to_sheet

    payload = []
    for item in ideas:
        source_text = item.get("source", "").strip()
        payload.append({
            "idea": item.get("idea", "").strip(),
            "source": f"{source_text} - {video_url}" if source_text else video_url,
            "source_type": source_type,
            "confidence_score": item.get("confidence_score", ""),
            "best_formats": item.get("best_formats", ""),
        })
    result = export_ideas_to_sheet(payload, spreadsheet_id=spreadsheet_id)
    return result.get("appended_count", len(payload))

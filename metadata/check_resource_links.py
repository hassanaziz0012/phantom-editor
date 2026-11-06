#!/usr/bin/env python3
"""
Resource Links Checker
======================
Reads a project's phrase-level `trimmed.srt` captions and asks Claude (via BrowserLLM)
to find every resource the video promises to link ("link in the description", "linked
below", ...). Saves them to the `resources` list in `metadata.json` as
`{"key", "url", "timestamp", "quote"}` objects with blank URLs for you to fill in.

`phantom projects status` flags resources whose URL is still blank, and `phantom publish yt`
lists them under RESOURCES in the description (and refuses to upload while any URL is blank).

Re-running keeps URLs you've already filled in: a resource whose key matches an existing
entry keeps that entry's URL, and existing entries with a URL that Claude didn't return
again are kept too.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Ensure repository root is in sys.path
repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

try:
    import metadata.utils as utils
except ImportError:
    import utils  # noqa: E402


def _key_id(key: str) -> str:
    return " ".join(key.lower().split())


def format_resources(data: Any) -> list[dict[str, str]]:
    """Normalizes Claude's response into `{"key", "url", "timestamp", "quote"}` entries."""
    if isinstance(data, dict):
        data = data.get("resources", data.get("links", []))

    if not isinstance(data, list):
        raise ValueError(f"Expected a list of resources, got: {type(data)}")

    results: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or item.get("name") or item.get("title") or "").strip()
        if not key or _key_id(key) in seen:
            continue
        seen.add(_key_id(key))
        results.append(
            {
                "key": key,
                "url": str(item.get("url") or "").strip(),
                "timestamp": str(item.get("timestamp") or "").strip().strip("[]()"),
                "quote": str(item.get("quote") or "").strip(),
            }
        )

    return results


def merge_resources(found: list[dict[str, str]], existing: Any) -> list[dict[str, str]]:
    """Carries URLs already filled in metadata.json over to the freshly found resources."""
    if not isinstance(existing, list):
        return found

    existing_entries = [e for e in existing if isinstance(e, dict) and str(e.get("key") or "").strip()]
    existing_urls = {
        _key_id(str(e["key"])): str(e.get("url") or "").strip() for e in existing_entries
    }

    merged: list[dict[str, str]] = []
    for entry in found:
        url = existing_urls.get(_key_id(entry["key"])) or entry["url"]
        merged.append({**entry, "url": url})

    # Keep filled-in entries Claude didn't return this time (e.g. ones you added by hand)
    found_ids = {_key_id(entry["key"]) for entry in found}
    for entry in existing_entries:
        if _key_id(str(entry["key"])) not in found_ids and str(entry.get("url") or "").strip():
            merged.append(entry)

    return merged


def blank_resources(resources: Any) -> list[str]:
    """Keys of the resources in a metadata.json `resources` list whose URL is still blank."""
    if not isinstance(resources, list):
        return []
    return [
        str(item.get("key") or "(unnamed)").strip()
        for item in resources
        if isinstance(item, dict) and not str(item.get("url") or "").strip()
    ]


def check_resource_links_for_project(
    target: str | Path | None = None,
    metadata_path: Path | None = None,
    profile: str | None = None,
) -> list[dict[str, str]]:
    """Finds the resources the video promises to link and saves them to metadata.json."""
    project_dir, default_meta_path, captions_path, title = utils.resolve_project_paths(target)
    save_meta_path = metadata_path or default_meta_path

    if not captions_path or not captions_path.is_file():
        raise FileNotFoundError(f"No phrase-level .srt captions (e.g. trimmed.srt) found in {project_dir}")

    print(f"📖 Reading captions from {captions_path.name}...")
    transcript = utils.parse_srt_to_timestamped_transcript(captions_path)

    print("🤖 Checking for promised resource links with Claude via BrowserLLM...")
    prompt = utils.load_prompt("check_resource_links.md", title=title, transcript=transcript)
    raw_response = utils.query_claude(prompt, model="sonnet", effort="high", output_json=True, profile=profile)

    found = format_resources(utils.parse_claude_json(raw_response))

    metadata: dict[str, Any] = {}
    if save_meta_path.is_file():
        try:
            metadata = json.loads(save_meta_path.read_text(encoding="utf-8"))
        except Exception:
            metadata = {}

    resources = merge_resources(found, metadata.get("resources"))
    metadata["resources"] = resources
    save_meta_path.parent.mkdir(parents=True, exist_ok=True)
    with open(save_meta_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=4, ensure_ascii=False)
        f.write("\n")

    return resources


def main():
    parser = argparse.ArgumentParser(
        description="Find the resources a video promises to link and save them to metadata.json's `resources` list."
    )
    parser.add_argument(
        "target",
        nargs="?",
        default=None,
        help="Path to the project folder, video file, or .srt captions file (default: current directory).",
    )
    parser.add_argument(
        "--metadata", "-m",
        default=None,
        help="Path to metadata.json (default: metadata.json in the project folder).",
    )
    parser.add_argument(
        "--profile",
        default=None,
        help="BrowserLLM browser profile to use (default: BrowserLLM's default).",
    )

    args = parser.parse_args()

    try:
        custom_meta = Path(args.metadata).resolve() if args.metadata else None
        resources = check_resource_links_for_project(args.target, metadata_path=custom_meta, profile=args.profile)

        if not resources:
            print("\n✅ The video doesn't promise to link anything. Saved an empty `resources` list.")
            return

        print(f"\n🔗 Found {len(resources)} resource(s) to link:")
        for item in resources:
            ts = f"[{item['timestamp']}] " if item.get("timestamp") else ""
            print(f"  {ts}{item['key']}: {item['url'] or '(URL needed)'}")
            if item.get("quote"):
                print(f"      \"{item['quote']}\"")

        missing = blank_resources(resources)
        if missing:
            print(f"\n✏️  Fill in the {len(missing)} blank URL(s) in the `resources` list of metadata.json before uploading.")
        else:
            print("\n✅ Every resource already has a URL.")
    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

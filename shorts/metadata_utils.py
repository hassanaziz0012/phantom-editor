import os
import sys
import json
import shutil
from pathlib import Path

def load_shorts_json(json_path):
    """Loads metadata from shorts.json. Returns a list of dicts."""
    if not os.path.exists(json_path):
        return []
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, list):
                print(f"Warning: {json_path} does not contain a list. Initializing as empty list.", file=sys.stderr)
                return []
            return data
    except json.JSONDecodeError as e:
        print(f"Error parsing {json_path}: {e}", file=sys.stderr)
        backup_path = f"{json_path}.bak"
        try:
            shutil.copy2(json_path, backup_path)
            print(f"Backed up corrupted JSON to {backup_path}", file=sys.stderr)
        except Exception as backup_err:
            print(f"Failed to create backup: {backup_err}", file=sys.stderr)
        return []

def save_shorts_json(json_path, data):
    """Saves metadata to shorts.json, ensuring the directory exists."""
    dir_name = os.path.dirname(os.path.abspath(json_path))
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def find_metadata_entry(shorts_data, video_path):
    """
    Intelligently find a video entry in shorts metadata by matching:
    1. Exact resolved absolute path
    2. Filename
    3. Stem
    """
    if not shorts_data:
        return None
    try:
        video_path_obj = Path(video_path).resolve()
    except Exception:
        return None

    # 1. Match by exact resolved path
    for entry in shorts_data:
        if isinstance(entry, dict) and "video_path" in entry:
            try:
                if Path(entry["video_path"]).resolve() == video_path_obj:
                    return entry
            except Exception:
                pass

    # 2. Match by filename
    for entry in shorts_data:
        if isinstance(entry, dict) and "video_path" in entry:
            try:
                if Path(entry["video_path"]).name == video_path_obj.name:
                    return entry
            except Exception:
                pass

    # 3. Match by stem
    for entry in shorts_data:
        if isinstance(entry, dict) and "video_path" in entry:
            try:
                if Path(entry["video_path"]).stem == video_path_obj.stem:
                    return entry
            except Exception:
                pass

    return None

def update_posted_status(shorts_json_path, video_path, platform, status_val=True):
    """Safely updates the posted status for a platform (youtube, instagram, tiktok) and writes it back."""
    shorts_json_path = Path(shorts_json_path)
    if not shorts_json_path.exists():
        raise FileNotFoundError(f"shorts.json file not found at '{shorts_json_path}'")
        
    try:
        with open(shorts_json_path, "r+", encoding="utf-8") as f:
            shorts_data = json.load(f)
            if not isinstance(shorts_data, list):
                raise ValueError("Metadata file content is not a list")
                
            entry = find_metadata_entry(shorts_data, video_path)
            if entry:
                if "posted" not in entry or not isinstance(entry["posted"], dict):
                    entry["posted"] = {"youtube": False, "instagram": False, "tiktok": False}
                entry["posted"][platform] = status_val
                
                f.seek(0)
                json.dump(shorts_data, f, indent=2, ensure_ascii=False)
                f.truncate()
                return True
            return False
    except Exception as e:
        sys.stderr.write(f"Warning: Failed to update shorts.json: {e}\n")
        raise e

def get_default_shorts_json_path():
    """Returns the repo-root shorts.json path (shorts/shorts.json)."""
    repo_root = Path(__file__).resolve().parent.parent
    return repo_root / "shorts" / "shorts.json"

def get_platform_entry(video_path, platform):
    """
    Shared uploader preamble for platform upload scripts (Instagram, TikTok, YouTube Shorts).

    Resolves the default shorts.json, loads it, finds the entry for video_path, and checks
    whether the video is already marked as posted on the given platform.

    Exits the process (with an error message) if shorts.json is missing or no entry exists.
    Returns (entry, shorts_json_path) on success; entry is None if already posted (after
    printing a skip notice).
    """
    video_path = Path(video_path)
    shorts_json_path = get_default_shorts_json_path()
    if not shorts_json_path.exists():
        print(f"Error: shorts.json not found at '{shorts_json_path}'", file=sys.stderr)
        sys.exit(1)

    shorts_data = load_shorts_json(str(shorts_json_path))
    metadata = find_metadata_entry(shorts_data, video_path)

    if not metadata:
        print(f"Error: Metadata for video '{video_path}' not found in shorts.json", file=sys.stderr)
        sys.exit(1)

    # Check if already posted to the target platform
    posted = metadata.get("posted")
    if isinstance(posted, dict) and posted.get(platform) is True:
        print(f"Video '{video_path.name}' is already marked as posted to {platform} in shorts.json. Skipping upload.")
        return None, shorts_json_path

    print("Found video metadata in shorts.json.")
    return metadata, shorts_json_path

def resolve_shorts_thumbnail(metadata, video_path):
    """
    Resolves the thumbnail path from a shorts.json entry.

    Relative paths are tried against the video's folder first, then the repo root.
    Prints a warning if a thumbnail is specified but cannot be found.
    Returns the resolved Path, or None if absent/unresolvable.
    """
    video_path = Path(video_path)
    repo_root = Path(__file__).resolve().parent.parent

    thumbnail_val = metadata.get("thumbnail")
    if not thumbnail_val:
        return None

    thumb_cand = Path(thumbnail_val)
    if not thumb_cand.is_absolute():
        resolved_thumb = video_path.parent / thumb_cand
        if not resolved_thumb.exists():
            resolved_thumb = repo_root / thumb_cand
    else:
        resolved_thumb = thumb_cand

    if resolved_thumb.exists() and resolved_thumb.is_file():
        return resolved_thumb

    print(f"Warning: Thumbnail file specified in metadata not found at '{resolved_thumb}'", file=sys.stderr)
    return None

def mark_posted(shorts_json_path, video_path, platform):
    """Marks a video as posted on the platform in shorts.json, with standard logging."""
    print("Updating posted status in shorts.json...")
    try:
        updated = update_posted_status(shorts_json_path, video_path, platform, True)
        if updated:
            print(f"Successfully updated posted.{platform} to true in shorts.json.")
        else:
            print(f"Warning: Could not find video entry in shorts.json to update posted status.", file=sys.stderr)
    except Exception as update_err:
        print(f"Warning: Failed to update shorts.json: {update_err}", file=sys.stderr)

def get_interactive_metadata(video_path):
    """Interactively prompts the user to specify title, description, tags, thumbnail, and scheduled time."""
    video_stem = Path(video_path).stem
    default_title = video_stem.replace("-", " ").replace("_", " ").title()
    
    print("\nPlease specify the metadata fields manually:")
    try:
        title = input(f"Title [{default_title}]: ").strip() or default_title
        description = input("Description: ").strip()
        tags_input = input("Tags (comma-separated): ").strip()
        tags = [t.strip() for t in tags_input.split(",") if t.strip()] if tags_input else []
        thumbnail = input("Thumbnail path (optional): ").strip()
        scheduled_time = input("Scheduled time (YYYY-MM-DDTHH:MM:SS, optional): ").strip()
        
        return {
            "title": title,
            "description": description,
            "tags": tags,
            "thumbnail": thumbnail,
            "scheduled_time": scheduled_time
        }
    except (KeyboardInterrupt, EOFError):
        print("\nAborted.")
        sys.exit(1)

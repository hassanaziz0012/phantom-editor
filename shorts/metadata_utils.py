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

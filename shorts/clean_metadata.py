#!/usr/bin/env python3
import os
import sys
import json
import shutil
from pathlib import Path

def load_shorts_json(json_path):
    """Loads metadata from shorts.json. Returns a list of dicts."""
    if not os.path.exists(json_path):
        print(f"Error: {json_path} does not exist.", file=sys.stderr)
        return []
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, list):
                print(f"Error: {json_path} does not contain a list.", file=sys.stderr)
                return []
            return data
    except json.JSONDecodeError as e:
        print(f"Error parsing {json_path}: {e}", file=sys.stderr)
        # Create a backup of the corrupted file
        backup_path = f"{json_path}.bak"
        try:
            shutil.copy2(json_path, backup_path)
            print(f"Backed up corrupted JSON to {backup_path}", file=sys.stderr)
        except Exception as backup_err:
            print(f"Failed to create backup: {backup_err}", file=sys.stderr)
        return []

def save_shorts_json(json_path, data):
    """Saves metadata to shorts.json."""
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def main():
    # Resolve shorts.json path relative to this script
    script_dir = Path(__file__).resolve().parent
    shorts_json_path = script_dir / "shorts.json"

    if not shorts_json_path.exists():
        print(f"No shorts.json found at {shorts_json_path}.", file=sys.stderr)
        sys.exit(1)

    shorts_data = load_shorts_json(str(shorts_json_path))
    if not shorts_data:
        print("No metadata items to process or failed to load data.")
        sys.exit(0)

    initial_count = len(shorts_data)
    cleaned_data = []
    removed_count = 0

    for entry in shorts_data:
        if not isinstance(entry, dict):
            cleaned_data.append(entry)
            continue

        posted = entry.get("posted")
        # An entry is fully posted if it has a 'posted' dict with values, and all values are True.
        if isinstance(posted, dict) and posted:
            if all(isinstance(v, bool) and v for v in posted.values()):
                # This item has been posted to all platforms. Skip it (remove it).
                removed_count += 1
                video_name = os.path.basename(entry.get("video_path", "unknown"))
                print(f"Clearing posted video: {video_name}")
                continue

        cleaned_data.append(entry)

    if removed_count > 0:
        # Create a backup before writing changes
        backup_path = f"{shorts_json_path}.bak"
        try:
            shutil.copy2(str(shorts_json_path), backup_path)
            print(f"Created a backup of shorts.json at {backup_path}")
        except Exception as backup_err:
            print(f"Warning: Failed to create backup before writing: {backup_err}", file=sys.stderr)

        save_shorts_json(str(shorts_json_path), cleaned_data)
        print(f"Clean complete. Removed {removed_count} item(s). {len(cleaned_data)} item(s) remaining in shorts.json.")
    else:
        print("No items were fully posted to all platforms. Nothing to clean.")

if __name__ == "__main__":
    main()

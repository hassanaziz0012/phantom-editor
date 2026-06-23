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

def is_fully_posted(entry):
    """Returns True if the entry has been posted to all platforms."""
    if not isinstance(entry, dict):
        return False
    posted = entry.get("posted")
    if isinstance(posted, dict) and posted:
        if all(isinstance(v, bool) and v for v in posted.values()):
            return True
    return False

def clean_metadata(shorts_json_path, interactive=False):
    """Cleans fully posted videos from shorts_json_path.
    If interactive is True, prompts user for confirmation.
    Returns the cleaned list of metadata.
    """
    shorts_data = load_shorts_json(shorts_json_path)
    if not shorts_data:
        return []

    fully_posted = [entry for entry in shorts_data if is_fully_posted(entry)]
    if not fully_posted:
        if not interactive:
            print("No items were fully posted to all platforms. Nothing to clean.")
        return shorts_data

    if interactive:
        print("\nThe following shorts have been posted on all platforms and will be removed:")
        for entry in fully_posted:
            video_name = os.path.basename(entry.get("video_path", "unknown"))
            print(f"  - {video_name}")
        try:
            confirm = input("\nDo you want to remove these fully posted items? [Y/n]: ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            print("\nExiting...")
            sys.exit(0)

        if confirm not in ("", "y", "yes"):
            print("Cleanup cancelled. Fully posted items will be kept.")
            return shorts_data

    # Perform cleaning
    cleaned_data = [entry for entry in shorts_data if not is_fully_posted(entry)]

    # Back up
    backup_path = f"{shorts_json_path}.bak"
    try:
        shutil.copy2(shorts_json_path, backup_path)
        print(f"Created a backup of shorts.json at {backup_path}")
    except Exception as backup_err:
        print(f"Warning: Failed to create backup before writing: {backup_err}", file=sys.stderr)

    save_shorts_json(shorts_json_path, cleaned_data)

    removed_count = len(fully_posted)
    if interactive:
        print(f"Clean complete. Removed {removed_count} item(s). {len(cleaned_data)} item(s) remaining in {os.path.basename(shorts_json_path)}.")
    else:
        # Match original clean_metadata.py outputs
        for entry in fully_posted:
            video_name = os.path.basename(entry.get("video_path", "unknown"))
            print(f"Clearing posted video: {video_name}")
        print(f"Clean complete. Removed {removed_count} item(s). {len(cleaned_data)} item(s) remaining in shorts.json.")

    return cleaned_data

def main():
    # Resolve shorts.json path relative to this script
    script_dir = Path(__file__).resolve().parent
    shorts_json_path = script_dir / "shorts.json"

    if not shorts_json_path.exists():
        print(f"No shorts.json found at {shorts_json_path}.", file=sys.stderr)
        sys.exit(1)

    clean_metadata(str(shorts_json_path), interactive=False)

if __name__ == "__main__":
    main()

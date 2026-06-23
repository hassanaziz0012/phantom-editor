import os
import sys
import json
import argparse
from datetime import datetime

# Import clean_metadata function from clean_metadata module
try:
    from clean_metadata import clean_metadata
except ImportError:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    from clean_metadata import clean_metadata

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
            import shutil
            shutil.copy2(json_path, backup_path)
            print(f"Backed up corrupted JSON to {backup_path}", file=sys.stderr)
        except Exception as backup_err:
            print(f"Failed to create backup: {backup_err}", file=sys.stderr)
        return []

def save_shorts_json(json_path, data):
    """Saves metadata to shorts.json, ensuring the directory exists."""
    # Ensure parent directory exists
    dir_name = os.path.dirname(os.path.abspath(json_path))
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def get_input(prompt, allow_skip=False, allow_empty=True):
    """Wrapper around input() to support quit ('q'/'quit') and skip ('s'/'skip') flags."""
    try:
        user_input = input(prompt).strip()
    except (KeyboardInterrupt, EOFError):
        print("\nExiting and saving progress...")
        sys.exit(0)

    # Global quit option
    if user_input.lower() in ("q", "quit"):
        print("\nExiting and saving progress...")
        sys.exit(0)

    # Skip option (either typing 'skip'/'s', or leaving blank if allowed)
    if allow_skip and (user_input.lower() in ("s", "skip") or (not user_input and allow_empty)):
        return None

    return user_input

def main():
    parser = argparse.ArgumentParser(description="Bulk generate metadata for short-form video pipeline.")
    parser.add_argument("folder", help="Folder containing MP4 video files.")
    parser.add_argument(
        "--shorts-json",
        default=None,
        help="Path to the shorts.json file. Defaults to shorts.json in the shorts/ directory."
    )

    args = parser.parse_args()

    # Validate target folder
    folder_path = os.path.abspath(args.folder)
    if not os.path.exists(folder_path):
        print(f"Error: The directory '{folder_path}' does not exist.", file=sys.stderr)
        sys.exit(1)
    if not os.path.isdir(folder_path):
        print(f"Error: '{folder_path}' is not a directory.", file=sys.stderr)
        sys.exit(1)

    # Determine shorts.json path
    if args.shorts_json is None:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        shorts_json_path = os.path.join(script_dir, "shorts.json")
    else:
        shorts_json_path = os.path.abspath(args.shorts_json)

    print(f"Target folder: {folder_path}")
    print(f"Shorts metadata file: {shorts_json_path}")

    # Clean the metadata first, prompt user if there are fully posted shorts
    shorts_data = clean_metadata(shorts_json_path, interactive=True)
    
    # Store existing paths as canonical absolute paths for lookup
    existing_paths = set()
    for item in shorts_data:
        if isinstance(item, dict) and "video_path" in item:
            existing_paths.add(os.path.abspath(item["video_path"]))

    # Scan for MP4 files in the folder (flat search)
    video_files = []
    try:
        for file in os.listdir(folder_path):
            if file.lower().endswith(".mp4"):
                full_path = os.path.join(folder_path, file)
                if os.path.isfile(full_path):
                    video_files.append(full_path)
    except Exception as e:
        print(f"Error scanning directory: {e}", file=sys.stderr)
        sys.exit(1)

    # Sort video files to process in a consistent order
    video_files.sort()

    # Filter out videos that are already added
    new_video_files = [v for v in video_files if os.path.abspath(v) not in existing_paths]

    if not new_video_files:
        print("No new MP4 video files found to process.")
        sys.exit(0)

    print(f"Found {len(video_files)} MP4 file(s) in total.")
    print(f"{len(video_files) - len(new_video_files)} file(s) already cataloged.")
    print(f"{len(new_video_files)} new file(s) ready for metadata input.")
    print("Type 'q' or 'quit' at any prompt to exit. Press Enter or type 's'/'skip' to skip current video.")

    for i, video_path in enumerate(new_video_files, start=1):
        filename = os.path.basename(video_path)
        abs_video_path = os.path.abspath(video_path)

        print(f"\n[{i}/{len(new_video_files)}] Processing: {filename}")
        print("-" * 50)

        # 1. Title (allow skip/empty)
        title = get_input("Title (leave blank / 's' to skip video): ", allow_skip=True, allow_empty=True)
        if title is None:
            print(f"Skipping video: {filename}")
            continue

        # 2. Description
        description = get_input("Description (press Enter to leave empty): ")
        if description is None:
            description = ""

        # 3. Thumbnail
        thumbnail = get_input("Thumbnail path/URL (press Enter to leave empty): ")
        if thumbnail is None:
            thumbnail = ""

        # 4. Tags (comma-separated list)
        tags_input = get_input("Tags (comma-separated, press Enter to leave empty): ")
        tags = []
        if tags_input:
            # Parse tags, remove duplicates, filter out empty strings
            seen_tags = set()
            for t in tags_input.split(","):
                cleaned = t.strip()
                if cleaned and cleaned not in seen_tags:
                    seen_tags.add(cleaned)
                    tags.append(cleaned)

        # 5. Scheduled Time (optional)
        scheduled_time = get_input("Scheduled Time (optional, e.g., YYYY-MM-DD HH:MM): ")
        if scheduled_time is None:
            scheduled_time = ""

        # Construct database entry
        entry = {
            "video_path": abs_video_path,
            "title": title,
            "description": description,
            "thumbnail": thumbnail,
            "tags": tags,
            "scheduled_time": scheduled_time,
            "added_at": datetime.now().isoformat(),
            "posted": {
                "youtube": False,
                "instagram": False,
                "tiktok": False
            }
        }

        # Save immediately to prevent progress loss
        shorts_data.append(entry)
        save_shorts_json(shorts_json_path, shorts_data)
        print(f"Saved metadata for '{filename}' to {os.path.basename(shorts_json_path)}")

    print("\nMetadata entry session finished successfully.")

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Create YouTube video metadata interactively.

Usage:
    phantom meta manual /path/to/video.mp4
    phantom meta manual <video_path>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


from shared.terminal import edit_text, highlight_json

# Enable GNU readline for interactive terminal editing (fixes arrow key escape sequences like ^[[D)
try:
    import readline
except ImportError:
    pass


def open_in_editor(initial_text: str = "") -> str:
    """Opens system editor with initial_text and returns the edited content."""
    try:
        return edit_text(initial_text).rstrip("\n")
    except Exception as e:
        print(f"⚠️ {e}. Keeping previous description.")
        return initial_text


def get_description() -> str:
    """Prompts for multi-paragraph description with full terminal line-editing and editor support."""
    if not sys.stdin.isatty():
        description_lines = []
        while True:
            try:
                line = input()
                if line.strip() == "EOF":
                    break
                description_lines.append(line)
            except EOFError:
                break
        return "\n".join(description_lines)

    print("\nDescription input options:")
    print(" • Type directly line-by-line (enter 'EOF' on a new line to finish)")
    print(" • Press Enter on empty line or type ':e' to open in text editor")

    description_lines = []
    first_line = True

    while True:
        try:
            prompt_str = "Description (press Enter or ':e' for editor, or start typing): " if first_line else "... "
            line = input(prompt_str)

            if first_line and (line.strip() == "" or line.strip().lower() == ":e"):
                text = open_in_editor("")
                if text:
                    description_lines = text.splitlines()
                break

            if line.strip().lower() == ":e":
                current_text = "\n".join(description_lines)
                text = open_in_editor(current_text)
                description_lines = text.splitlines()
                break

            if line.strip() == "EOF":
                break

            description_lines.append(line)
            first_line = False
        except (EOFError, KeyboardInterrupt):
            print()
            break

    # Review & edit loop
    while True:
        description_text = "\n".join(description_lines)
        print("\n" + "-" * 40)
        print("📄 Description Preview:")
        if not description_lines:
            print("  (Empty description)")
        else:
            for idx, line in enumerate(description_lines, 1):
                print(f"  {idx:2d} | {line}")
        print("-" * 40)

        choice = input("Description Action [C(ontinue) / e(dit in editor) / l(ine edit) / a(ppend) / r(ewrite)]: ").strip().lower()
        if choice in ["", "c", "continue", "y", "yes"]:
            break
        elif choice in ["e", "editor", "edit"]:
            new_text = open_in_editor(description_text)
            description_lines = new_text.splitlines()
        elif choice in ["l", "line"]:
            if not description_lines:
                print("No lines to edit yet.")
                continue
            try:
                line_no_str = input(f"Line number to edit (1-{len(description_lines)}): ").strip()
                line_no = int(line_no_str)
                if 1 <= line_no <= len(description_lines):
                    old_line = description_lines[line_no - 1]
                    print(f"Current line {line_no}: {old_line}")
                    new_line = input(f"New text for line {line_no}: ")
                    description_lines[line_no - 1] = new_line
                else:
                    print("Invalid line number.")
            except ValueError:
                print("Please enter a valid line number.")
        elif choice in ["a", "append"]:
            print("Type additional lines (Enter 'EOF' to finish):")
            while True:
                try:
                    line = input("... ")
                    if line.strip() == "EOF":
                        break
                    description_lines.append(line)
                except (EOFError, KeyboardInterrupt):
                    print()
                    break
        elif choice in ["r", "rewrite"]:
            description_lines = []
            print("Enter new description (type 'EOF' or ':e' for editor to finish):")
            first_line = True
            while True:
                try:
                    prompt_str = "Description (press Enter or ':e' for editor, or start typing): " if first_line else "... "
                    line = input(prompt_str)
                    if first_line and (line.strip() == "" or line.strip().lower() == ":e"):
                        text = open_in_editor("")
                        description_lines = text.splitlines()
                        break
                    if line.strip().lower() == ":e":
                        text = open_in_editor("\n".join(description_lines))
                        description_lines = text.splitlines()
                        break
                    if line.strip() == "EOF":
                        break
                    description_lines.append(line)
                    first_line = False
                except (EOFError, KeyboardInterrupt):
                    print()
                    break

    return "\n".join(description_lines)


def get_tweet_template(title: str = "") -> str:
    """Prompts for tweet template and ensures the rendered tweet does not exceed 280 characters."""
    default_tweet = "🎬 New video just dropped! {url}"
    sample_url = "https://youtu.be/OmV52jkTnjE"

    while True:
        tweet_template = input(f"Tweet Template [{default_tweet}]: ").strip() or default_tweet

        try:
            rendered = tweet_template.format(url=sample_url, title=title)
        except (KeyError, IndexError, ValueError):
            # Unknown placeholders or stray braces: show the two known ones filled in
            rendered = tweet_template.replace("{url}", sample_url).replace("{title}", title)

        rendered_len = len(rendered)
        if rendered_len > 280:
            print(
                f"❌ Error: Tweet is too long ({rendered_len}/280 characters including YouTube URL, "
                f"{rendered_len - 280} over limit). Please enter a shorter template.\n"
            )
            continue

        return tweet_template


def main():
    parser = argparse.ArgumentParser(description="Create metadata.json for a YouTube video.")
    parser.add_argument("video_path", help="Path to the .mp4 file.")
    args = parser.parse_args()

    video_path = Path(args.video_path).resolve()

    # Grab project name from the parent directory
    project_name = video_path.parent.name
    print(f"🎬 Project: {project_name}")
    print(f"Video file: {video_path.name}")
    print("-" * 40)
    print("Please provide the following metadata (press Enter to use the default):")

    default_title = video_path.stem
    title = input(f"Title [{default_title}]: ").strip() or default_title

    description = get_description()

    tags_input = input("Tags (comma-separated) []: ").strip()
    tags = [tag.strip() for tag in tags_input.split(",") if tag.strip()]

    category_id = input("Category ID [28 (Science & Technology)]: ").strip() or "28"

    privacy_status = input("Privacy Status (public, private, unlisted) [public]: ").strip().lower() or "public"
    if privacy_status not in ["public", "private", "unlisted"]:
        print(f"Warning: '{privacy_status}' is not a standard privacy status, defaulting to 'public'")
        privacy_status = "public"

    made_for_kids_input = input("Made for Kids? (y/N) [N]: ").strip().lower()
    made_for_kids = made_for_kids_input in ['y', 'yes']

    tweet_template = get_tweet_template(title=title)

    metadata = {
        "title": title,
        "description": description,
        "tags": tags,
        "categoryId": category_id,
        "privacyStatus": privacy_status,
        "madeForKids": made_for_kids,
        "tweetTemplate": tweet_template
    }

    metadata_path = video_path.parent / "metadata.json"

    # Display summary
    print("\n" + "=" * 40)
    print("Generated Metadata:")
    formatted_json = json.dumps(metadata, indent=4, ensure_ascii=False)
    print(highlight_json(formatted_json))
    print("=" * 40)

    confirm = input(f"Write to {metadata_path}? [Y/n]: ").strip().lower()
    if confirm in ['', 'y', 'yes']:
        with open(metadata_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=4, ensure_ascii=False)
        print(f"\n✅ Created {metadata_path} successfully!")
    else:
        print("\n❌ Aborted. No file was written.")


if __name__ == "__main__":
    main()


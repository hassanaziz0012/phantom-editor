#!/usr/bin/env python3
import argparse
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from content.written_content_sheet_utils import append_written_post

PLATFORM_CONFIG = {
    "linkedin": {
        "title": "LINKEDIN POST",
        "prompt_path": REPO_ROOT / "agentic" / "prompts" / "write_linkedin_post_from_yapping.md",
        "char_info": lambda post: f"Characters: {len(post)} / 3000 | Words: {len(post.split())}",
    },
    "substack": {
        "title": "SUBSTACK NOTE",
        "prompt_path": REPO_ROOT / "agentic" / "prompts" / "write_substack_note_from_yapping.md",
        "char_info": lambda post: f"Characters: {len(post)} | Words: {len(post.split())}",
    },
}


def get_yapping_from_editor() -> str:
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "nano"
    cmd = shlex.split(editor)

    with tempfile.NamedTemporaryFile(suffix=".txt", prefix="yap_", delete=False) as tf:
        temp_path = tf.name

    try:
        if Path(cmd[0]).name == "gnome-text-editor" and "--standalone" not in cmd:
            cmd.append("--standalone")
        elif Path(cmd[0]).name == "code" and "-w" not in cmd and "--wait" not in cmd:
            cmd.append("--wait")

        print(f"Opening {editor}... write your thoughts, then save and close.")
        subprocess.call(cmd + [temp_path])

        with open(temp_path, "r", encoding="utf-8") as f:
            return f.read().strip()
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def parse_response(text: str) -> Tuple[str, str]:
    # Extract from markdown code fence if wrapped
    match = re.search(r"```(?:markdown|md|text)?\s*([\s\S]*?)\s*```", text.strip())
    content = match.group(1).strip() if match else text.strip()

    # Split post and image ideas by horizontal rule
    parts = re.split(r"\n\s*[-*_]{3,}\s*\n", content, maxsplit=1)
    post = parts[0].strip()
    image_ideas = parts[1].strip() if len(parts) > 1 else ""

    return post, image_ideas


def copy_clipboard(text: str) -> bool:
    for tool, args in [("wl-copy", []), ("xclip", ["-selection", "clipboard"]), ("pbcopy", [])]:
        if shutil.which(tool):
            subprocess.run([tool] + args, input=text.encode("utf-8"), check=False)
            return True
    return False


def generate_post(raw_yap: str, platform: str) -> Tuple[str, str]:
    config = PLATFORM_CONFIG[platform]
    prompt_path = config["prompt_path"]
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt template not found at {prompt_path}")

    prompt_template = prompt_path.read_text(encoding="utf-8")
    filled_prompt = prompt_template.replace("{textwall}", raw_yap)

    print("Generating post with Claude via BrowserLLM...")
    with tempfile.NamedTemporaryFile("w+", suffix=".txt", delete=False) as pf, \
         tempfile.NamedTemporaryFile("r+", suffix=".txt", delete=False) as of:
        prompt_file, output_file = pf.name, of.name
        pf.write(filled_prompt)
        pf.flush()

    try:
        browserllm = shutil.which("browserllm") or str(Path.home() / ".local" / "bin" / "browserllm")
        subprocess.run([browserllm, "-p", prompt_file, "--provider", "claude", "-o", output_file], check=True)
        response = Path(output_file).read_text(encoding="utf-8").strip()
    finally:
        for p in (prompt_file, output_file):
            if os.path.exists(p):
                os.remove(p)

    return parse_response(response)


def main():
    parser = argparse.ArgumentParser(description="Convert yapping into platform-specific social posts.")
    parser.add_argument(
        "-p", "--platform",
        choices=["linkedin", "substack"],
        help="Target platform (linkedin or substack).",
    )
    parser.add_argument(
        "platform_pos",
        nargs="?",
        choices=["linkedin", "substack"],
        help="Target platform (positional alternative).",
    )
    parser.add_argument(
        "-f", "--file",
        type=Path,
        help="Optional path to a file containing the raw yapping text.",
    )
    parser.add_argument(
        "-t", "--text",
        type=str,
        help="Optional raw yapping text string.",
    )
    parser.add_argument(
        "--no-sheet",
        action="store_true",
        help="Skip saving the generated post to Google Sheets Content Calendar.",
    )
    parser.add_argument(
        "--sheet-id",
        type=str,
        default=None,
        help="Optional Google Sheets ID (defaults to CONTENT_CALENDAR_SHEET_ID in .env).",
    )

    args = parser.parse_args()
    platform = args.platform or args.platform_pos

    if not platform:
        parser.error("Platform is required. Specify with --platform linkedin|substack or as positional argument.")

    platform = platform.lower()
    config = PLATFORM_CONFIG[platform]

    if args.text:
        raw_yap = args.text.strip()
    elif args.file:
        if not args.file.exists():
            print(f"Error: File not found at {args.file}")
            sys.exit(1)
        raw_yap = args.file.read_text(encoding="utf-8").strip()
    else:
        raw_yap = get_yapping_from_editor()

    if not raw_yap:
        print("No text entered. Aborting.")
        sys.exit(0)

    post, image_ideas = generate_post(raw_yap, platform)

    print("\n" + "=" * 60)
    print(config["title"])
    print("=" * 60)
    print(post)
    print("=" * 60)
    print(config["char_info"](post))

    if image_ideas:
        print("\n" + "-" * 60)
        print("IMAGE IDEAS")
        print("-" * 60)
        print(image_ideas)
        print("-" * 60)

    if copy_clipboard(post):
        print(f"\n✓ Copied {platform} post to clipboard!")

    if not args.no_sheet:
        try:
            record = append_written_post(
                post=post,
                platform=platform,
                media=image_ideas,
                status="Draft",
                spreadsheet_id=args.sheet_id,
            )
            print(f"✓ Saved to Google Sheet ('Written' tab, row {record.row_index}, Status: {record.status})")
        except Exception as e:
            print(f"⚠️  Could not save to Google Sheet: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()

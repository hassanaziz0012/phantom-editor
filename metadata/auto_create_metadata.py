#!/usr/bin/env python3
"""
Automatic YouTube Metadata Generator
====================================
Automatically creates or updates `metadata.json` in a YouTube video project folder.
If captions (.srt) are found, automatically generates:
1. Human-like 2-3 sentence video description
2. Promotional tweet template
3. Video recommendations from channel archive
4. Video chapters & timestamps
5. Scored video title ideas
6. Resources the video promises to link (blank URLs for you to fill in)
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, TypeVar

import metadata.utils as utils
from metadata.auto_gen_desc import generate_description_for_project
from metadata.auto_gen_tweet import generate_tweet_for_project
from metadata.recommendations.recommend import recommend_videos_for_project
from metadata.generate_timestamps import generate_timestamps_for_project
from metadata.generate_titles import generate_titles_for_project
from metadata.check_resource_links import blank_resources, check_resource_links_for_project


T = TypeVar("T")


def retry_with_backoff(
    func: Callable[[], T],
    max_retries: int = 3,
    base_delay: float = 2.0,
    step_name: str = "operation",
) -> T | None:
    """Execute a function with retry logic and exponential backoff."""
    for attempt in range(1, max_retries + 1):
        try:
            return func()
        except Exception as e:
            if attempt < max_retries:
                delay = base_delay * (2 ** (attempt - 1))
                print(f"⚠️  {step_name} failed (attempt {attempt}/{max_retries}): {e}")
                print(f"   Retrying in {delay:.1f}s...")
                time.sleep(delay)
            else:
                print(f"❌ {step_name} failed after {max_retries} attempts: {e}")
                return None
    return None


def ensure_browserllm_ready(profile: str | None = None) -> bool:
    """Run `browserllm blank` to ensure browser window is open, then wait 1s."""
    print("\n🌐 Ensuring BrowserLLM browser window is open...")
    cmd = ["browserllm", "blank"]
    if profile:
        cmd.extend(["--profile", profile])
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=30)
        print("   BrowserLLM window opened successfully.")
    except subprocess.CalledProcessError as e:
        print(f"   ⚠️  browserllm blank failed (exit code {e.returncode}): {e.stderr.strip()}")
        print("   Continuing anyway - browser may already be open.")
    except FileNotFoundError:
        print("   ⚠️  'browserllm' command not found in PATH. Continuing anyway.")
    except subprocess.TimeoutExpired:
        print("   ⚠️  browserllm blank timed out after 30s. Continuing anyway.")
    
    print("   Waiting 1s for browser to stabilize...")
    time.sleep(1)
    return True


def save_metadata(metadata_path: Path, metadata: dict[str, Any]) -> str:
    """Write metadata.json and return the JSON written."""
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    formatted_json = json.dumps(metadata, indent=4, ensure_ascii=False)
    metadata_path.write_text(formatted_json + "\n", encoding="utf-8")
    return formatted_json


def auto_create_metadata(
    target: str | Path | None = None,
    title: str | None = None,
    tags: list[str] | None = None,
    category_id: str | None = None,
    privacy_status: str | None = None,
    made_for_kids: bool | None = None,
    skip_ai: bool = False,
    skip_recommendations: bool = False,
    profile: str | None = None,
) -> dict[str, Any]:
    """Creates or updates metadata.json for the targeted video project.

    Settings left as None keep the value already in metadata.json (or the default).
    """
    project_dir, metadata_path, captions_path, default_title = utils.resolve_project_paths(target)

    # Load existing metadata if present
    metadata = utils.load_metadata_json(metadata_path)
    if metadata_path.is_file():
        print(f"🔄 Updating existing metadata at: {metadata_path}")
    else:
        print(f"✨ Creating new metadata at: {metadata_path}")

    # Set base properties
    final_title = title or metadata.get("title") or default_title
    metadata["title"] = final_title
    metadata.setdefault("description", "")
    metadata["tags"] = tags if tags is not None else metadata.get("tags", [])
    metadata["categoryId"] = category_id or metadata.get("categoryId", "28")
    metadata["privacyStatus"] = privacy_status or metadata.get("privacyStatus", "public")
    metadata["madeForKids"] = made_for_kids if made_for_kids is not None else metadata.get("madeForKids", False)
    metadata.setdefault("tweetTemplate", "🎬 New video just dropped! {url}")
    metadata.setdefault("recommendations", metadata.get("recommendations", []))
    metadata.setdefault("timestamps", metadata.get("timestamps", []))
    metadata.setdefault("titles", metadata.get("titles", []))

    # Write the base fields first: the generators below read the title from metadata.json
    save_metadata(metadata_path, metadata)

    if not skip_ai:
        if captions_path and captions_path.is_file():
            print(f"\n📂 Found captions file: {captions_path.name}")

            # Ensure BrowserLLM browser is ready before running parallel workers
            ensure_browserllm_ready(profile)

            # Define the AI generation tasks
            def run_description() -> str | None:
                return retry_with_backoff(
                    lambda: generate_description_for_project(captions_path, metadata_path=metadata_path, profile=profile),
                    max_retries=3,
                    base_delay=2.0,
                    step_name="Description generation",
                )

            def run_tweet() -> str | None:
                return retry_with_backoff(
                    lambda: generate_tweet_for_project(captions_path, metadata_path=metadata_path, profile=profile),
                    max_retries=3,
                    base_delay=2.0,
                    step_name="Tweet template generation",
                )

            def run_recommendations() -> list[dict[str, Any]] | None:
                # Recommendations embed the description. Without waiting for it, a new
                # project has none yet and recommend.py would generate a second one.
                description = futures["description"].result() if "description" in futures else None
                return retry_with_backoff(
                    lambda: recommend_videos_for_project(
                        target=project_dir, metadata_path=metadata_path, description=description, profile=profile
                    ),
                    max_retries=3,
                    base_delay=2.0,
                    step_name="Recommendations generation",
                )

            def run_timestamps() -> list[dict[str, str]] | None:
                return retry_with_backoff(
                    lambda: generate_timestamps_for_project(target=project_dir, metadata_path=metadata_path, profile=profile),
                    max_retries=3,
                    base_delay=2.0,
                    step_name="Timestamps generation",
                )

            def run_titles() -> list[dict[str, Any]] | None:
                return retry_with_backoff(
                    lambda: generate_titles_for_project(captions_path, metadata_path=metadata_path, profile=profile),
                    max_retries=3,
                    base_delay=2.0,
                    step_name="Titles generation",
                )

            def run_resources() -> list[dict[str, str]] | None:
                return retry_with_backoff(
                    lambda: check_resource_links_for_project(captions_path, metadata_path=metadata_path, profile=profile),
                    max_retries=3,
                    base_delay=2.0,
                    step_name="Resource links check",
                )

            # Prepare tasks to run
            tasks: dict[str, Callable[[], Any]] = {
                "description": run_description,
                "tweet": run_tweet,
                "timestamps": run_timestamps,
                "titles": run_titles,
                "resources": run_resources,
            }
            if not skip_recommendations:
                tasks["recommendations"] = run_recommendations

            print(f"\n🚀 Running {len(tasks)} AI generation tasks in parallel...")
            
            # Run all tasks in parallel using ThreadPoolExecutor
            results: dict[str, Any] = {}
            futures: dict[str, concurrent.futures.Future] = {}
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(tasks)) as executor:
                for task_name, task_fn in tasks.items():
                    futures[task_name] = executor.submit(task_fn)
                future_to_task = {future: task_name for task_name, future in futures.items()}

                for future in concurrent.futures.as_completed(future_to_task):
                    task_name = future_to_task[future]
                    try:
                        result = future.result()
                        results[task_name] = result
                        if result is not None:
                            if task_name == "recommendations":
                                print(f"✓ [{task_name}] Generated {len(result)} recommendations.")
                            elif task_name == "timestamps":
                                print(f"✓ [{task_name}] Generated {len(result)} timestamps.")
                            elif task_name == "titles":
                                print(f"✓ [{task_name}] Generated {len(result)} titles.")
                            elif task_name == "resources":
                                print(f"✓ [{task_name}] Found {len(result)} resources to link.")
                            else:
                                print(f"✓ [{task_name}] Generated successfully.")
                        else:
                            print(f"⚠️  [{task_name}] Failed after max retries.")
                    except Exception as e:
                        print(f"❌ [{task_name}] Unexpected error: {e}")
                        results[task_name] = None

            # Update metadata with results
            if results.get("description") is not None:
                metadata["description"] = results["description"]
            if results.get("tweet") is not None:
                metadata["tweetTemplate"] = results["tweet"]
            if results.get("recommendations") is not None:
                metadata["recommendations"] = results["recommendations"]
            if results.get("timestamps") is not None:
                metadata["timestamps"] = results["timestamps"]
            if results.get("titles") is not None:
                metadata["titles"] = results["titles"]
            if results.get("resources") is not None:
                metadata["resources"] = results["resources"]

        else:
            print("\n⚠️  No phrase-level .srt captions file found in project folder.")
            print("   Skipping AI generation of description, tweet template, recommendations, timestamps, titles, and resources.")
            print("   Run `phantom edit transcribe-cloud <video>` to generate captions first if needed.")

    formatted_json = save_metadata(metadata_path, metadata)

    print("\n" + "=" * 50)
    print("📄 Saved Metadata Summary:")
    print("=" * 50)
    print(utils.highlight_json(formatted_json))
    print("=" * 50)
    print(f"\n✅ Successfully wrote {metadata_path}")

    blank = blank_resources(metadata.get("resources"))
    if blank:
        print(f"\n🔗 The video promises {len(blank)} link(s) that still need a URL in `resources`:")
        for key in blank:
            print(f"   - {key}")

    return metadata


def main():
    parser = argparse.ArgumentParser(
        description="Automatically create or update metadata.json for a YouTube video project."
    )
    parser.add_argument(
        "target",
        nargs="?",
        default=None,
        help="Path to the video file, captions file, or project folder (default: current directory).",
    )
    parser.add_argument("--title", "-t", default=None, help="Custom title for the video.")
    parser.add_argument("--tags", default=None, help="Comma-separated tags for the video.")
    # These default to None so a re-run keeps what metadata.json already says
    parser.add_argument(
        "--category-id",
        default=None,
        help="YouTube Category ID (default: keep existing, else 28 for Tech).",
    )
    parser.add_argument(
        "--privacy-status",
        choices=["public", "private", "unlisted"],
        default=None,
        help="Privacy status (default: keep existing, else public).",
    )
    parser.add_argument(
        "--made-for-kids",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Whether the video is made for kids (default: keep existing, else no).",
    )
    parser.add_argument(
        "--skip-ai",
        action="store_true",
        default=False,
        help="Skip AI generation of description, tweet template, recommendations, timestamps, titles, and resources.",
    )
    parser.add_argument(
        "--skip-recommendations",
        action="store_true",
        default=False,
        help="Skip generation of video recommendations.",
    )
    parser.add_argument(
        "--profile",
        default=None,
        help="BrowserLLM browser profile to use (default: BrowserLLM's default).",
    )

    args = parser.parse_args()

    tags_list = [tag.strip() for tag in args.tags.split(",") if tag.strip()] if args.tags else None

    try:
        auto_create_metadata(
            target=args.target,
            title=args.title,
            tags=tags_list,
            category_id=args.category_id,
            privacy_status=args.privacy_status,
            made_for_kids=args.made_for_kids,
            skip_ai=args.skip_ai,
            skip_recommendations=args.skip_recommendations,
            profile=args.profile,
        )
    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()


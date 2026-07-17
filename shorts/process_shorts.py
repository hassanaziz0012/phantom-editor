#!/usr/bin/env python3
"""
Shorts Pipeline Orchestrator
Chains together silence trimming, auto-trimming clips, audio processing, BGM addition,
manual verification, thumbnail prepending, auto-captioning, and interactive metadata creation.
"""

import os
import sys
import argparse
import subprocess
import shutil
from pathlib import Path

def run_command(cmd, desc=""):
    if desc:
        print(f"\n=======================================================")
        print(f"🚀 {desc.upper()}")
        print(f"=======================================================")
    print(f"Running command: {' '.join(str(x) for x in cmd)}\n")
    
    try:
        # Run subprocess directly connected to stdout/stderr so progress is shown
        result = subprocess.run(cmd, check=True)
        return result.returncode == 0
    except subprocess.CalledProcessError as e:
        print(f"\n❌ Error: Command failed with exit code {e.returncode}", file=sys.stderr)
        sys.exit(e.returncode)
    except FileNotFoundError as e:
        print(f"\n❌ Error: Executable or script not found. {e}", file=sys.stderr)
        sys.exit(1)

def main():
    parser = argparse.ArgumentParser(
        description="Unified Shorts Production Pipeline: Raw Video -> Fully Processed & Cataloged Shorts."
    )
    
    # Core parameters
    parser.add_argument(
        "raw_video",
        help="Path to the raw input video file."
    )
    parser.add_argument(
        "--bgm-track",
        required=True,
        help="BGM track name or full path to add to the video."
    )
    parser.add_argument(
        "--bgm-volume",
        type=int,
        default=10,
        help="Volume percentage (1-100) for the BGM (default: 10)."
    )
    parser.add_argument(
        "--output-dir",
        "-O",
        default=None,
        help="Custom workspace directory path. Defaults to a folder named after the video stem under the video's parent directory."
    )
    parser.add_argument(
        "--skip-silence",
        action="store_true",
        help="Skip the silence-trimming step and use the raw video directly for clipping."
    )
    
    # Silence trimming options
    silence_group = parser.add_argument_group("Silence Trimming Options (Step 1)")
    silence_group.add_argument(
        "--silence-threshold",
        type=float,
        default=0.5,
        help="Speech threshold for Silero VAD. Probabilities above this value are considered speech (default: 0.5)."
    )
    silence_group.add_argument(
        "--silence-padding",
        type=float,
        default=0.15,
        help="Padding in seconds to add to start/end of speech intervals (default: 0.15)."
    )
    silence_group.add_argument(
        "--silence-min",
        type=float,
        default=0.4,
        help="Minimum silence duration in seconds to split segments (default: 0.4)."
    )
    
    # Clip trimming options
    trim_group = parser.add_argument_group("Clips Trimming Options (Step 2)")
    trim_group.add_argument(
        "--trim-padding",
        type=float,
        default=0.5,
        help="Seconds of pre/post-roll buffer added to each clip cut (default: 0.5)."
    )
    trim_group.add_argument(
        "--min-confidence",
        type=float,
        default=70.0,
        help="Fuzzy-match confidence score threshold (0-100) (default: 70.0)."
    )
    trim_group.add_argument(
        "--override",
        action="append",
        default=[],
        help="Bypass detection for a short. Format: slug=START,END. Repeatable flag."
    )
    trim_group.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute and print the match/cut plan without cutting clips (will exit pipeline early)."
    )
    trim_group.add_argument(
        "--manual",
        action="store_true",
        help="Interactive manual mode: prompts for start times of each short."
    )
    
    # Thumbnail options
    thumb_group = parser.add_argument_group("Thumbnail Options (Step 6)")
    thumb_group.add_argument(
        "--thumb-font-size",
        type=int,
        default=None,
        help="Custom font size override for the cover text."
    )
    thumb_group.add_argument(
        "--thumb-duration",
        type=float,
        default=0.25,
        help="Duration in seconds to display the cover frame (default: 0.25s)."
    )
    
    # Captioning options
    caption_group = parser.add_argument_group("Auto Captioning Options (Step 7)")
    caption_group.add_argument(
        "--caption-model",
        choices=["small", "medium", "large"],
        default="medium",
        help="Whisper model size to use locally for captioning (default: medium)."
    )
    caption_group.add_argument(
        "--caption-max-words",
        type=int,
        default=None,
        help="Maximum words per caption segment."
    )
    caption_group.add_argument(
        "--caption-font-size",
        type=int,
        default=None,
        help="Font size for the burned captions (default: 16)."
    )
    caption_group.add_argument(
        "--caption-bottom-margin",
        type=int,
        default=None,
        help="Bottom margin for the burned captions in pixels."
    )
    caption_group.add_argument(
        "--caption-width",
        type=int,
        default=20,
        help="Maximum line width in characters for text wrapping (default: 20)."
    )
    caption_group.add_argument(
        "--caption-preset",
        choices=["shorts"],
        default=None,
        help="Apply a predefined set of caption styling options."
    )
    caption_group.add_argument(
        "--caption-font",
        type=str,
        default=None,
        help="Font name to use for the captions (e.g. 'Google Sans', 'Arial', 'Impact')."
    )
    caption_group.add_argument(
        "--caption-uppercase",
        action="store_true",
        default=None,
        help="Convert captions to uppercase."
    )
    caption_group.add_argument(
        "--caption-no-uppercase",
        action="store_true",
        default=None,
        help="Disable converting captions to uppercase."
    )
    caption_group.add_argument(
        "--caption-vad-filter",
        action="store_true",
        default=None,
        help="Use VAD filter to ignore silences during transcription."
    )
    caption_group.add_argument(
        "--caption-no-vad-filter",
        action="store_true",
        default=None,
        help="Disable VAD filter."
    )
    
    # Metadata options
    metadata_group = parser.add_argument_group("Metadata Options (Step 8)")
    metadata_group.add_argument(
        "--shorts-json",
        default=None,
        help="Path to the shorts.json file. Defaults to shorts.json in the shorts/ directory."
    )
    
    args = parser.parse_args()
    
    # Resolve script paths
    shorts_dir = Path(__file__).resolve().parent
    repo_root = shorts_dir.parent
    
    trim_silences_script = repo_root / "video-editing" / "trim_silences.py"
    auto_trim_script = shorts_dir / "auto_trim_shorts.py"
    process_audio_script = repo_root / "audio-processing" / "process_audio.sh"
    add_bgm_script = repo_root / "video-editing" / "add_bgm_to_video.sh"
    add_thumbnail_script = shorts_dir / "add_thumbnail.py"
    auto_caption_script = repo_root / "video-editing" / "auto_caption.py"
    create_metadata_script = shorts_dir / "create_bulk_metadata.py"
    
    # Resolve raw video path
    raw_video_path = Path(args.raw_video).resolve()
    if not raw_video_path.is_file():
        print(f"❌ Error: Raw video file not found at '{raw_video_path}'", file=sys.stderr)
        sys.exit(1)
        
    # Resolve workspace path
    if args.output_dir:
        workspace_dir = Path(args.output_dir).resolve()
    else:
        workspace_dir = raw_video_path.parent / raw_video_path.stem
        
    print(f"\n📁 Pipeline Workspace: {workspace_dir}")
    
    # Workspace paths
    trimmed_dir = workspace_dir / "trimmed"
    audio_processed_dir = workspace_dir / "audio-processed"
    added_bgm_dir = workspace_dir / "added_bgm"
    thumbnail_prepended_dir = workspace_dir / "thumbnail_prepended"
    captioned_dir = workspace_dir / "captioned"
    
    # Setup trimmed directory (ensure it is clean for this run)
    if trimmed_dir.exists():
        print(f"🧹 Cleaning existing trimmed workspace: {trimmed_dir}")
        shutil.rmtree(trimmed_dir)
    trimmed_dir.mkdir(parents=True, exist_ok=True)
    
    silence_trimmed_video = trimmed_dir / "silence_trimmed.mp4"
    
    # -------------------------------------------------------------------------
    # STEP 1: Trim Silences
    # -------------------------------------------------------------------------
    if args.skip_silence:
        print("\n⏭️  Skipping Step 1: Silence Trimming as requested.")
        print(f"Copying raw video to '{silence_trimmed_video}'...")
        shutil.copy2(raw_video_path, silence_trimmed_video)
    else:
        cmd_silence = [
            sys.executable,
            str(trim_silences_script),
            str(raw_video_path),
            "-o", str(silence_trimmed_video),
            "--threshold", str(args.silence_threshold),
            "--padding", str(args.silence_padding),
            "--min-silence", str(args.silence_min)
        ]
        run_command(cmd_silence, "Step 1: Trimming silences from raw video")
        
    # -------------------------------------------------------------------------
    # STEP 2: Trim Clips (Cut Shorts)
    # -------------------------------------------------------------------------
    cmd_trim = [
        sys.executable,
        str(auto_trim_script),
        "--video", str(silence_trimmed_video),
        "--padding", str(args.trim_padding),
        "--min-confidence", str(args.min_confidence)
    ]
    if args.override:
        for val in args.override:
            cmd_trim.extend(["--override", val])
    if args.dry_run:
        cmd_trim.append("--dry-run")
    if args.manual:
        cmd_trim.append("--manual")
        
    run_command(cmd_trim, "Step 2: Auto-trimming clips from video")
    
    # Handle Dry Run user choice interaction
    if args.dry_run:
        print("\n=======================================================")
        print("🔍 DRY RUN COMPLETE: CHOOSE PIPELINE ACTION")
        print("=======================================================")
        print("Please review the Cut Plan printed above.")
        print("Select how you would like to proceed:")
        print("  1. Proceed with the timestamps determined by the script.")
        print("  2. Provide manual overrides for the shorts.")
        print("  3. Rerun trimming in interactive manual mode.")
        print("  4. Abort the pipeline and exit.")
        
        while True:
            try:
                choice = input("\nEnter choice (1-4): ").strip()
                if choice not in ("1", "2", "3", "4"):
                    print("⚠️  Invalid choice. Please enter a number between 1 and 4.")
                    continue
                break
            except (KeyboardInterrupt, EOFError):
                print("\nPipeline aborted.")
                sys.exit(0)
                
        if choice == "4":
            print("\nAborting pipeline. Workspace files remain.")
            sys.exit(0)
            
        # Re-build trimming command without dry-run
        cmd_trim_actual = [
            sys.executable,
            str(auto_trim_script),
            "--video", str(silence_trimmed_video),
            "--padding", str(args.trim_padding),
            "--min-confidence", str(args.min_confidence)
        ]
        
        if choice == "1":
            # Just run auto-trim with original overrides (if any)
            if args.override:
                for val in args.override:
                    cmd_trim_actual.extend(["--override", val])
            if args.manual:
                cmd_trim_actual.append("--manual")
                
        elif choice == "2":
            # Collect manual overrides
            entered_overrides = list(args.override)
            print("\nEnter overrides in the format: slug=START,END (e.g. 'intro-clip=10.5,42.0')")
            print("Refer to the Cut Plan slugs printed above. Press Enter on an empty line when done.")
            while True:
                try:
                    ov = input("Add override: ").strip()
                    if not ov:
                        break
                    if "=" not in ov or "," not in ov:
                        print("⚠️  Invalid format. Must be slug=START,END (e.g. intro-clip=12.3,45.6)")
                        continue
                    entered_overrides.append(ov)
                except (KeyboardInterrupt, EOFError):
                    print("\nAborting override entry. Exiting.")
                    sys.exit(0)
            
            for val in entered_overrides:
                cmd_trim_actual.extend(["--override", val])
            if args.manual:
                cmd_trim_actual.append("--manual")
                
        elif choice == "3":
            # Run in manual interactive mode
            if args.override:
                for val in args.override:
                    cmd_trim_actual.extend(["--override", val])
            cmd_trim_actual.append("--manual")
            
        run_command(cmd_trim_actual, "Executing actual video trimming cuts")
        
    if silence_trimmed_video.exists():
        silence_trimmed_video.unlink()
        
    # Delete Whisper captions file if it was created inside trimmed/
    srt_pattern = f"{silence_trimmed_video.stem}-1word.srt"
    legacy_srt_pattern = "captions_1word.srt"
    for filename in [srt_pattern, legacy_srt_pattern]:
        srt_file = trimmed_dir / filename
        if srt_file.exists():
            srt_file.unlink()
            
    # Verify we actually have cut clips to process
    mp4_clips = list(trimmed_dir.glob("*.mp4"))
    if not mp4_clips:
        print("\n⚠️  No shorts were cut during Step 2. Terminating pipeline.", file=sys.stderr)
        sys.exit(0)
        
    # -------------------------------------------------------------------------
    # STEP 3: Process Audio
    # -------------------------------------------------------------------------
    cmd_audio = [
        "bash",
        str(process_audio_script),
        "--recursive",
        str(trimmed_dir)
    ]
    run_command(cmd_audio, "Step 3: Processing audio of all clips")
    
    # -------------------------------------------------------------------------
    # STEP 4: Add BGM
    # -------------------------------------------------------------------------
    cmd_bgm = [
        "bash",
        str(add_bgm_script),
        str(audio_processed_dir),
        args.bgm_track,
        "--volume", str(args.bgm_volume),
        "--recursive"
    ]
    run_command(cmd_bgm, f"Step 4: Adding BGM '{args.bgm_track}' (volume {args.bgm_volume}%) to clips")
    
    # -------------------------------------------------------------------------
    # STEP 5: Manual Pause for Verification
    # -------------------------------------------------------------------------
    print("\n=======================================================")
    print("⏸️  PIPELINE PAUSED: MANUAL REVIEW REQUIRED")
    print("=======================================================")
    print(f"All BGM-added shorts have been created in:")
    print(f"  {added_bgm_dir}")
    print("\nPlease verify the clips manually. You can delete incorrect takes,")
    print("re-cut bad segments, or modify files directly in that directory.")
    print("Once you are finished and want to generate thumbnails and captions, enter 'Y'.")
    print("To abort the pipeline and exit, enter 'N'.")
    
    try:
        user_choice = input("\nProceed to thumbnailing and captioning? (Y/N): ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        print("\nPipeline aborted.")
        sys.exit(0)
        
    if user_choice not in ("y", "yes"):
        print("\nPipeline aborted. Outputs remain in your workspace.")
        sys.exit(0)
        
    # -------------------------------------------------------------------------
    # STEP 6: Prepend Thumbnails
    # -------------------------------------------------------------------------
    cmd_thumb = [
        sys.executable,
        str(add_thumbnail_script),
        str(added_bgm_dir),
        "--recursive"
    ]
    if args.thumb_font_size is not None:
        cmd_thumb.extend(["--font-size", str(args.thumb_font_size)])
    if args.thumb_duration is not None:
        cmd_thumb.extend(["--duration", str(args.thumb_duration)])
        
    run_command(cmd_thumb, "Step 6: Generating and prepending thumbnails to shorts")
    
    # -------------------------------------------------------------------------
    # STEP 7: Auto Captioning
    # -------------------------------------------------------------------------
    cmd_caption = [
        sys.executable,
        str(auto_caption_script),
        str(thumbnail_prepended_dir),
        "--recursive",
        "--model", args.caption_model,
        "--width", str(args.caption_width)
    ]
    if args.caption_max_words is not None:
        cmd_caption.extend(["--max-words", str(args.caption_max_words)])
    if args.caption_font_size is not None:
        cmd_caption.extend(["--font-size", str(args.caption_font_size)])
    if args.caption_bottom_margin is not None:
        cmd_caption.extend(["--bottom-margin", str(args.caption_bottom_margin)])
    if args.caption_preset is not None:
        cmd_caption.extend(["--preset", args.caption_preset])
    if args.caption_font is not None:
        cmd_caption.extend(["--font", args.caption_font])
        
    # Uppercase handling
    if args.caption_uppercase is True:
        cmd_caption.append("--uppercase")
    elif args.caption_no_uppercase is True:
        cmd_caption.append("--no-uppercase")
        
    # VAD filter handling
    if args.caption_vad_filter is True:
        cmd_caption.append("--vad-filter")
    elif args.caption_no_vad_filter is True:
        cmd_caption.append("--no-vad-filter")
        
    run_command(cmd_caption, "Step 7: Generating and burning auto-captions onto shorts")
    
    # -------------------------------------------------------------------------
    # STEP 8: Create Bulk Metadata Catalog
    # -------------------------------------------------------------------------
    cmd_metadata = [
        sys.executable,
        str(create_metadata_script),
        str(captioned_dir)
    ]
    if args.shorts_json is not None:
        cmd_metadata.extend(["--shorts-json", str(args.shorts_json)])
        
    run_command(cmd_metadata, "Step 8: Launching interactive bulk metadata cataloging")
    
    print("\n=======================================================")
    print("🎉 PIPELINE RUN COMPLETED SUCCESSFULLY!")
    print("=======================================================")
    print(f"Final production-ready captioned shorts are in:\n  {captioned_dir}")
    print("\nAll entries have been cataloged in your metadata database.")

if __name__ == "__main__":
    main()

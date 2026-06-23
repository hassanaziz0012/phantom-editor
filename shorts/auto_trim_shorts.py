#!/usr/bin/env python3
"""
Automated Shorts Cutter
Usage: python auto_trim_shorts.py --video raw_video.mp4 [options]
"""

import os
import sys
import re
import argparse
import subprocess
from pathlib import Path
from dotenv import load_dotenv

# Add project root and video-editing directory to sys.path
repo_root = Path(__file__).resolve().parent.parent
video_editing_dir = repo_root / "video-editing"
if str(repo_root) not in sys.path:
    sys.path.append(str(repo_root))
if str(video_editing_dir) not in sys.path:
    sys.path.append(str(video_editing_dir))

# Load environment variables
load_dotenv(repo_root / ".env")

# Import local transcribe utility and shared utilities
from transcribe import transcribe_video
from utils import (
    format_srt_time,
    parse_timestamp,
    slugify,
    get_google_doc_shorts,
    find_client_secrets,
    GDOCS_TOKEN_FILE,
)

# Fuzzy matching and optimal assignment
from rapidfuzz import fuzz
from scipy.optimize import linear_sum_assignment
import numpy as np

# ---------------------------------------------------------------------------
# Configuration & Constants
# ---------------------------------------------------------------------------

# Google Doc ID is hardcoded/loaded as a constant from the environment
GOOGLE_DOC_ID = os.environ.get("SHORTS_GOOGLE_DOC_ID")

# ---------------------------------------------------------------------------
# Step 2: Parse Captions
# ---------------------------------------------------------------------------

def parse_srt(srt_path: Path):
    """Parses SRT captions into a list of (start_time, end_time, text) tuples."""
    if not srt_path.exists():
        raise FileNotFoundError(f"SRT captions file not found: {srt_path}")
        
    with open(srt_path, "r", encoding="utf-8") as f:
        content = f.read()
        
    content = content.replace('\r\n', '\n').strip()
    # SRT blocks are separated by double newlines
    blocks = re.split(r'\n\s*\n', content)
    
    captions = []
    for block in blocks:
        lines = block.strip().split('\n')
        if len(lines) < 3:
            continue
        
        time_line = lines[1]
        match = re.match(r'(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,\.]\d{3})', time_line)
        if not match:
            continue
            
        start_t = parse_timestamp(match.group(1))
        end_t = parse_timestamp(match.group(2))
        text = " ".join(lines[2:]).strip()
        captions.append((start_t, end_t, text))
        
    return captions


# ---------------------------------------------------------------------------
# Step 3: Segment Boundary Detection (Gap Detection)
# ---------------------------------------------------------------------------

def detect_segment_boundaries(captions, num_shorts: int, sanity_floor: float = 3.0):
    """Computes gaps between captions and divides timeline into N candidate segments."""
    n = num_shorts
    if n <= 1:
        # If N=1, no boundary division is needed
        if captions:
            return [{
                "captions": captions,
                "start_time": captions[0][0],
                "end_time": captions[-1][1],
                "text": " ".join(c[2] for c in captions),
                "start_cap_idx": 0,
                "end_cap_idx": len(captions) - 1
            }]
        return []

    needed_gaps = n - 1
    valid_gaps = []
    
    for i in range(len(captions) - 1):
        current_end = captions[i][1]
        next_start = captions[i+1][0]
        gap_dur = next_start - current_end
        if gap_dur >= sanity_floor:
            valid_gaps.append({
                "index": i,  # Boundary index is the caption index after which the cut occurs
                "duration": gap_dur,
                "start_time": current_end,
                "end_time": next_start
            })
            
    if len(valid_gaps) < needed_gaps:
        # Not enough gaps above sanity floor
        return None

    # Sort gaps descending and pick the largest N-1
    valid_gaps.sort(key=lambda x: x["duration"], reverse=True)
    top_gaps = valid_gaps[:needed_gaps]
    
    # Sort chronologically
    top_gaps.sort(key=lambda x: x["index"])
    
    segments = []
    start_idx = 0
    for gap in top_gaps:
        cut_idx = gap["index"]
        seg_caps = captions[start_idx : cut_idx + 1]
        if seg_caps:
            segments.append({
                "captions": seg_caps,
                "start_time": seg_caps[0][0],
                "end_time": seg_caps[-1][1],
                "text": " ".join(c[2] for c in seg_caps),
                "start_cap_idx": start_idx,
                "end_cap_idx": cut_idx
            })
        start_idx = cut_idx + 1
        
    # Final segment
    seg_caps = captions[start_idx:]
    if seg_caps:
        segments.append({
            "captions": seg_caps,
            "start_time": seg_caps[0][0],
            "end_time": seg_caps[-1][1],
            "text": " ".join(c[2] for c in seg_caps),
            "start_cap_idx": start_idx,
            "end_cap_idx": len(captions) - 1
        })
        
    return segments


# ---------------------------------------------------------------------------
# Step 4: Match Segments to Titles
# ---------------------------------------------------------------------------

def match_segments(shorts, segments):
    """Computes a fuzzy matching matrix and solves as a linear sum assignment problem."""
    num_shorts = len(shorts)
    num_segments = len(segments)
    
    score_matrix = np.zeros((num_shorts, num_segments))
    for i, short in enumerate(shorts):
        short_content = f"{short['title']} {short['body']}"
        for j, segment in enumerate(segments):
            # Compute token-set ratio between script content and concatenated segment captions
            score = fuzz.token_set_ratio(short_content, segment["text"])
            score_matrix[i, j] = score
            
    # scipy.optimize.linear_sum_assignment minimizes the sum of costs.
    # Therefore, cost_matrix = 100 - score_matrix.
    cost_matrix = 100.0 - score_matrix
    
    row_ind, col_ind = linear_sum_assignment(cost_matrix)
    
    matches = {}
    for r, c in zip(row_ind, col_ind):
        matches[r] = {
            "segment_idx": c,
            "confidence": score_matrix[r, c]
        }
        
    return matches


def extract_title_keywords(title: str) -> list[str]:
    """Extracts meaningful words from a short title for keyword scanning."""
    STOPWORDS = {
        "a", "an", "the", "in", "on", "at", "to", "for", "of", "and",
        "or", "but", "is", "are", "was", "were", "be", "been", "being",
        "with", "by", "from", "that", "this", "it", "its", "why", "how",
        "what", "when", "will", "just", "than", "then", "their", "they",
        "we", "our", "not", "no", "so", "if", "as", "up", "do", "did",
        "who", "which", "more", "about", "might", "could", "would", "x"
    }
    words = re.findall(r"[a-zA-Z0-9]+", title.lower())
    return [w for w in words if w not in STOPWORDS and len(w) > 2]


def find_keyword_start(captions: list, start_idx: int, end_idx: int, keywords: list[str], window: int = 6) -> int:
    """
    Scans captions[start_idx:end_idx+1] and returns the index of the first caption
    where any keyword appears within a sliding window of `window` consecutive words.
    Falls back to start_idx if no match is found.
    """
    if not keywords:
        return start_idx

    # Build a list of (caption_idx, word) pairs for the window
    words_in_range = []
    for i in range(start_idx, end_idx + 1):
        word = captions[i][2].strip().lower().rstrip(".,!?;:'\"")
        words_in_range.append((i, word))

    for pos in range(len(words_in_range)):
        window_words = {w for _, w in words_in_range[pos: pos + window]}
        if any(kw in window_words for kw in keywords):
            return words_in_range[pos][0]

    return start_idx  # fallback: no keyword found

# ---------------------------------------------------------------------------
# Step 5 & 6: Overrides, Warning Output & Video Cutting
# ---------------------------------------------------------------------------

# (Shared utility function slugify is imported from utils.py)


def print_low_confidence_warning(short, slug, segment, confidence, captions, start_idx, end_idx, min_confidence):
    """Prints a detailed low-confidence warning displaying surrounding caption lines context."""
    print(f"\n⚠️  [LOW CONFIDENCE] Match for short: '{short['title']}'")
    print(f"   Slug: {slug}")
    print(f"   Confidence score: {confidence:.1f} (threshold: {min_confidence})")
    print(f"   Detected segment range: {format_srt_time(segment['start_time'])} --> {format_srt_time(segment['end_time'])}")
    print("   Surrounding Captions context:")
    
    # Preceding context
    prec_start = max(0, start_idx - 2)
    for idx in range(prec_start, start_idx):
        c = captions[idx]
        print(f"     [Prev] [{format_srt_time(c[0])} -> {format_srt_time(c[1])}] {c[2]}")
        
    # Segment captions
    print("     ---------------- MATCHED SEGMENT ----------------")
    for idx in range(start_idx, end_idx + 1):
        c = captions[idx]
        print(f"     *      [{format_srt_time(c[0])} -> {format_srt_time(c[1])}] {c[2]}")
    print("     -------------------------------------------------")
    
    # Following context
    foll_end = min(len(captions), end_idx + 3)
    for idx in range(end_idx + 1, foll_end):
        c = captions[idx]
        print(f"     [Next] [{format_srt_time(c[0])} -> {format_srt_time(c[1])}] {c[2]}")


def print_no_segment_warning(short, slug, min_confidence):
    """Prints warning when gap detection fails and segments cannot be extracted."""
    print(f"\n⚠️  [LOW CONFIDENCE] Match for short: '{short['title']}'")
    print(f"   Slug: {slug}")
    print(f"   Confidence score: 0.0 (threshold: {min_confidence})")
    print("   Reason: Gap detection failed (fewer than N-1 gaps cleared sanity floor).")
    print("   Please use the override flag to supply manually determined timestamps.")
    print("   Script snippet:")
    snippet = short["body"][:200] + "..." if len(short["body"]) > 200 else short["body"]
    print(f"     {snippet}")


def get_video_duration(video_path: Path) -> float:
    """Gets the duration of the video file in seconds using ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(video_path)
    ]
    try:
        result = subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return float(result.stdout.strip())
    except subprocess.CalledProcessError as e:
        print(f"❌ ffprobe failed to get video duration: {e.stderr}")
        raise
    except FileNotFoundError:
        print("❌ Error: ffprobe binary was not found. Please install ffmpeg/ffprobe.")
        sys.exit(1)


def run_ffmpeg_cut(video_path: Path, start_time: float, end_time: float, padding: float, output_path: Path):
    """Invokes ffmpeg to cut out a precise video range using re-encoding to prevent black screens."""
    padded_start = max(0.0, start_time - padding)
    padded_end = end_time + padding
    
    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{padded_start:.3f}",
        "-to", f"{padded_end:.3f}",
        "-i", str(video_path),
        "-c:v", "libx264",
        "-crf", "18",
        "-c:a", "aac",
        str(output_path)
    ]
    
    print(f"\n🎬 Cutting '{output_path.name}' ({format_srt_time(padded_start)} -> {format_srt_time(padded_end)})")
    
    try:
        # Running ffmpeg subprocess securely without shell=True to avoid injection
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"✅ Created: {output_path}")
    except subprocess.CalledProcessError as e:
        print(f"❌ ffmpeg execution failed for {output_path.name} (exit code: {e.returncode})")
        raise
    except FileNotFoundError:
        print("❌ Error: ffmpeg binary was not found. Please install ffmpeg.")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Main Execution Flow
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Deterministic Automated Shorts Cutter CLI")
    parser.add_argument(
        "--video",
        required=True,
        help="Path to the raw long-form video file."
    )
    parser.add_argument(
        "--padding",
        type=float,
        default=0.5,
        help="Seconds of pre/post-roll buffer added to each cut (default: 0.5)"
    )
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=70.0,
        help="Fuzzy-match confidence score threshold (0-100) (default: 70.0)"
    )
    parser.add_argument(
        "--override",
        action="append",
        default=[],
        help="Bypass detection for a short. Format: slug=START,END. Repeatable flag."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute and print the match/cut plan without invoking ffmpeg or writing files."
    )
    parser.add_argument(
        "--manual",
        action="store_true",
        help="Interactive manual mode: prompts for start times of each short."
    )
    
    args = parser.parse_args()
    
    # Resolve paths & validate existence
    video_path = Path(args.video).resolve()
    
    if not video_path.is_file():
        print(f"❌ Error: Video file does not exist at '{video_path}'")
        sys.exit(1)
        
    # Setup Google Doc credentials
    client_secret_file = find_client_secrets()
    
    # 1. Fetch Shorts from Google Docs
    try:
        shorts = get_google_doc_shorts(GOOGLE_DOC_ID, client_secret_file, GDOCS_TOKEN_FILE)
    except Exception as e:
        print(f"❌ Error fetching Google Doc script contents: {e}")
        sys.exit(1)
        
    N = len(shorts)
    print(f"📋 Found {N} short scripts outlined in the Google Doc.")
    if N == 0:
        print("❌ Error: No shorts scripts found (make sure scripts are headings with HEADING_2 style).")
        sys.exit(1)

    if args.manual:
        print("\n🧠 Manual interactive mode selected.")
        try:
            video_duration = get_video_duration(video_path)
        except Exception as e:
            print(f"❌ Failed to get video duration: {e}")
            sys.exit(1)
        print(f"📹 Video duration: {format_srt_time(video_duration)} ({video_duration:.3f} seconds)")

        start_times = []
        for idx, short in enumerate(shorts):
            title = short["title"]
            print(f"\n[{idx + 1}/{N}] Short Title: '{title}'")
            while True:
                try:
                    val = input("   Enter start time (e.g. 12.3 or 00:01:23,456): ").strip()
                    if not val:
                        print("   ⚠️  Start time cannot be empty. Please enter a valid timestamp.")
                        continue
                    start_t = parse_timestamp(val)
                    if start_t < 0:
                        print("   ⚠️  Start time cannot be negative.")
                        continue
                    if start_t > video_duration:
                        print(f"   ⚠️  Start time ({format_srt_time(start_t)}) cannot be after video duration ({format_srt_time(video_duration)}).")
                        continue
                    if start_times and start_t < start_times[-1]:
                        print(f"   ⚠️  Start time cannot be before previous short's start time ({format_srt_time(start_times[-1])}).")
                        continue
                    start_times.append(start_t)
                    break
                except ValueError as err:
                    print(f"   ⚠️  {err}")

        # Build cut jobs
        cut_jobs = []
        for idx, short in enumerate(shorts):
            title = short["title"]
            slug = slugify(title)
            start_t = start_times[idx]
            end_t = start_times[idx + 1] if idx + 1 < N else video_duration
            
            cut_jobs.append({
                "short": short,
                "slug": slug,
                "start": start_t,
                "end": end_t,
                "source": "Manual input"
            })
            
        flagged_jobs = []

    else:
        # Auto-generate captions file path
        video_dir = video_path.parent
        video_name_without_ext = video_path.stem
        captions_path = video_dir / f"{video_name_without_ext}-1word.srt"
        
        # Auto-transcribe if the captions file does not exist
        if not captions_path.is_file():
            print(f"📝 Auto-transcribing video file '{video_path}' using Whisper 'medium' model...")
            try:
                transcribe_video(
                    video_path=str(video_path),
                    model_path_or_size="medium",
                    output_srt_path=str(captions_path),
                    max_words=1,
                    uppercase=False,
                    preview=False,
                    vad_filter=True
                )
            except Exception as e:
                print(f"❌ Error during auto-transcription: {e}")
                sys.exit(1)
        else:
            print(f"📖 Using existing captions file at '{captions_path}'")
            
        # Parse overrides
        overrides = {}
        for ov_str in args.override:
            if "=" not in ov_str:
                print(f"⚠️  Skipping invalid override string format: '{ov_str}' (expected slug=START,END)")
                continue
            slug, times = ov_str.split("=", 1)
            if "," not in times:
                print(f"⚠️  Skipping invalid override timestamps: '{times}' for slug '{slug}' (expected START,END)")
                continue
            start_str, end_str = times.split(",", 1)
            try:
                start_t = parse_timestamp(start_str)
                end_t = parse_timestamp(end_str)
                overrides[slug.strip()] = (start_t, end_t)
            except ValueError as err:
                print(f"⚠️  Error parsing timestamps in override '{ov_str}': {err}")
                continue

        # 2. Parse Captions
        try:
            captions = parse_srt(captions_path)
        except Exception as e:
            print(f"❌ Error parsing SRT file: {e}")
            sys.exit(1)
            
        print(f"💬 Parsed {len(captions)} caption intervals from SRT.")
        if not captions:
            print("❌ Error: Captions file contains no valid subtitle intervals.")
            sys.exit(1)
            
        # 3. Detect Segment Boundaries
        segments = detect_segment_boundaries(captions, N)
        
        # 4. Perform Bipartite Matching
        matches = {}
        if segments is not None:
            print(f"✂️  Segment boundary detection split video timeline into {len(segments)} candidate segments.")
            matches = match_segments(shorts, segments)
        else:
            print(f"⚠️  Could not automatically segment video timeline (insufficient gaps >3.0s).")

        # 5. Review Matches, Refine Start Times & Apply Overrides
        # First pass: collect all keyword-refined start times so we can use
        # short[i+1]'s start as short[i]'s end.
        refined = {}  # idx -> {start_cap_idx, end_cap_idx, start_time, end_time, confidence}

        if segments is not None:
            for idx, short in enumerate(shorts):
                if idx not in matches:
                    continue
                match_info = matches[idx]
                seg_idx = match_info["segment_idx"]
                confidence = match_info["confidence"]
                segment = segments[seg_idx]

                keywords = extract_title_keywords(short["title"])
                true_start_idx = find_keyword_start(
                    captions,
                    segment["start_cap_idx"],
                    segment["end_cap_idx"],
                    keywords
                )
                refined[idx] = {
                    "start_cap_idx": true_start_idx,
                    "end_cap_idx": segment["end_cap_idx"],
                    "start_time": captions[true_start_idx][0],
                    "end_time": segment["end_time"],
                    "confidence": confidence,
                    "segment": segment,
                }

            # Second pass: set each short's end_time = the refined start of the next short
            sorted_idxs = sorted(refined.keys())
            for i, idx in enumerate(sorted_idxs):
                if i + 1 < len(sorted_idxs):
                    next_idx = sorted_idxs[i + 1]
                    refined[idx]["end_time"] = captions[refined[next_idx]["start_cap_idx"]][0]
                    refined[idx]["end_cap_idx"] = refined[next_idx]["start_cap_idx"] - 1

        cut_jobs = []
        flagged_jobs = []

        for idx, short in enumerate(shorts):
            title = short["title"]
            slug = slugify(title)

            if slug in overrides:
                start_t, end_t = overrides[slug]
                cut_jobs.append({
                    "short": short,
                    "slug": slug,
                    "start": start_t,
                    "end": end_t,
                    "source": "Override flag"
                })
            elif segments is None or idx not in refined:
                print_no_segment_warning(short, slug, args.min_confidence)
                flagged_jobs.append((short, slug, None, 0.0, "No segments detected"))
            else:
                r = refined[idx]
                confidence = r["confidence"]

                if confidence >= args.min_confidence:
                    cut_jobs.append({
                        "short": short,
                        "slug": slug,
                        "start": r["start_time"],
                        "end": r["end_time"],
                        "source": f"Keyword-refined (confidence {confidence:.1f})"
                    })
                else:
                    print_low_confidence_warning(
                        short=short,
                        slug=slug,
                        segment=r["segment"],
                        confidence=confidence,
                        captions=captions,
                        start_idx=r["start_cap_idx"],
                        end_idx=r["end_cap_idx"],
                        min_confidence=args.min_confidence
                    )
                    flagged_jobs.append((short, slug, r["segment"], confidence, f"Low confidence {confidence:.1f}"))
                    
    # Display Cut Plan Summary
    print("\n=======================================================")
    print("🎬                     CUT PLAN                        ")
    print("=======================================================")
    for job in cut_jobs:
        padded_start = max(0.0, job["start"] - args.padding)
        padded_end = job["end"] + args.padding
        print(f"👉 SHORT: '{job['short']['title']}'")
        print(f"   Filename: {job['slug']}.mp4")
        print(f"   Source:   {job['source']}")
        print(f"   Times:    {format_srt_time(job['start'])} --> {format_srt_time(job['end'])}")
        print(f"   Padded:   {format_srt_time(padded_start)} --> {format_srt_time(padded_end)} (+/- {args.padding}s)")
        print("-------------------------------------------------------")
        
    if flagged_jobs:
        print("\n=======================================================")
        print("⚠️                  FLAGGED SEGMENTS                    ")
        print("=======================================================")
        print(f"The following {len(flagged_jobs)} segments need manual review (scores below threshold or no segments):")
        for f_short, f_slug, f_seg, f_conf, f_reason in flagged_jobs:
            print(f"❌ {f_short['title']} (slug: {f_slug})")
            print(f"   Reason: {f_reason}")
            if f_seg:
                print(f"   Detected segment: {format_srt_time(f_seg['start_time'])} --> {format_srt_time(f_seg['end_time'])}")
        print("=======================================================")
        
    # Dry run execution check
    if args.dry_run:
        print("\n✨ Dry run enabled. No videos cut, no files written.")
        sys.exit(0)
        
    if not cut_jobs:
        print("\nℹ️  No confirmed cut plans. Use --override slug=START,END to cut flagged segments.")
        sys.exit(0)
        
    # Perform cuts
    print(f"\n🎥 Proceeding to cut {len(cut_jobs)} shorts with ffmpeg...")
    output_dir = video_path.parent
    
    for job in cut_jobs:
        output_file = output_dir / f"{job['slug']}.mp4"
        run_ffmpeg_cut(
            video_path=video_path,
            start_time=job["start"],
            end_time=job["end"],
            padding=args.padding,
            output_path=output_file
        )
        
    print("\n🎉 Automated short cutting complete!")


if __name__ == "__main__":
    main()

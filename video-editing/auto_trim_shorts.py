#!/usr/bin/env python3
"""
Automated Shorts Cutter
Usage: python auto_trim_shorts.py --video raw_video.mp4 --captions captions.srt [options]
"""

import os
import sys
import re
import argparse
import subprocess
from pathlib import Path
from dotenv import load_dotenv

# Add project root to sys.path
repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.append(str(repo_root))

# Load environment variables
load_dotenv(repo_root / ".env")

# Google API client library imports
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# Fuzzy matching and optimal assignment
from rapidfuzz import fuzz
from scipy.optimize import linear_sum_assignment
import numpy as np

# ---------------------------------------------------------------------------
# Configuration & Constants
# ---------------------------------------------------------------------------

SCOPES = ["https://www.googleapis.com/auth/documents.readonly"]

# Google Doc ID is hardcoded/loaded as a constant from the environment
GOOGLE_DOC_ID = os.environ.get("SHORTS_GOOGLE_DOC_ID")

# OAuth token and secrets files
GDOCS_TOKEN_FILE = Path(__file__).resolve().parent / "tokens/gdocs_token.json"


def find_client_secrets():
    """Locate client_secret.json across common directories."""
    # 1. video-editing/tokens/client_secret.json
    p1 = Path(__file__).resolve().parent / "tokens/client_secret.json"
    if p1.exists():
        return p1
    # 2. youtube_api/tokens/client_secret.json
    p2 = Path(__file__).resolve().parent.parent / "youtube_api/tokens/client_secret.json"
    if p2.exists():
        return p2
    # 3. root tokens/client_secret.json
    p3 = Path(__file__).resolve().parent.parent / "tokens/client_secret.json"
    if p3.exists():
        return p3
    return p1  # Default fallback path


# ---------------------------------------------------------------------------
# Timestamps & Formatting Helpers
# ---------------------------------------------------------------------------

def format_srt_time(seconds: float) -> str:
    """Converts seconds to SRT time format: HH:MM:SS,mmm"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    milliseconds = int(round((seconds % 1) * 1000))
    if milliseconds == 1000:
        milliseconds = 0
        secs += 1
        if secs == 60:
            secs = 0
            minutes += 1
            if minutes == 60:
                minutes = 0
                hours += 1
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"


def parse_timestamp(val: str) -> float:
    """Parses float seconds or HH:MM:SS,mmm formatted timestamps into float seconds."""
    val = val.strip()
    if not val:
        return 0.0
    try:
        return float(val)
    except ValueError:
        pass
    
    val = val.replace(',', '.')
    parts = val.split(':')
    if len(parts) == 3:
        # HH:MM:SS.mmm
        h = int(parts[0])
        m = int(parts[1])
        s = float(parts[2])
        return h * 3600 + m * 60 + s
    elif len(parts) == 2:
        # MM:SS.mmm
        m = int(parts[0])
        s = float(parts[1])
        return m * 60 + s
    else:
        raise ValueError(f"Invalid timestamp format: '{val}'. Expected float seconds or HH:MM:SS,mmm")


def prompt_for_timestamps(title: str) -> tuple[float, float]:
    """Prompts the user to enter start and end timestamps for a short."""
    while True:
        try:
            val = input(f"\n👉 Enter start and end timestamps for '{title}' (e.g. '01:23,456 01:54,321' or '83.4 114.3'): ").strip()
            if not val:
                print("❌ Input cannot be empty.")
                continue
            
            # First split by whitespace
            parts = val.split()
            if len(parts) != 2:
                # If no whitespace, try splitting by comma (e.g., 12.5,14.2)
                if "," in val and " " not in val:
                    # Note: we need to be careful with HH:MM:SS,mmm formats.
                    # If they typed HH:MM:SS,mmm,HH:MM:SS,mmm without spaces, splitting by comma yields 4 parts.
                    # If there's only one comma, it's definitely a separator: e.g., 12.5,14.2
                    if val.count(",") == 1:
                        parts = val.split(",")
                    # If there are three commas, e.g., 00:01:02,300,00:01:05,400
                    elif val.count(",") == 3:
                        comma_indices = [i for i, c in enumerate(val) if c == ',']
                        if len(comma_indices) == 3:
                            # The middle one separates the two timestamps
                            mid_comma = comma_indices[1]
                            parts = [val[:mid_comma], val[mid_comma+1:]]
                
            if len(parts) != 2:
                print("❌ Invalid format. Please enter exactly two timestamps separated by a space.")
                continue
                
            start_t = parse_timestamp(parts[0])
            end_t = parse_timestamp(parts[1])
            if start_t >= end_t:
                print("❌ Start timestamp must be less than end timestamp.")
                continue
            return start_t, end_t
        except ValueError as e:
            print(f"❌ Error parsing timestamps: {e}. Please try again.")
        except (KeyboardInterrupt, EOFError):
            print("\n👋 Prompt aborted by user.")
            sys.exit(1)


# ---------------------------------------------------------------------------
# Step 1: Pull Script from Google Docs
# ---------------------------------------------------------------------------

def get_google_doc_shorts(doc_id: str, credentials_file: Path, token_file: Path):
    """Fetches the Google Doc content and parses it into {title, body} shorts."""
    if not doc_id:
        raise ValueError("Google Doc ID is missing. Set SHORTS_GOOGLE_DOC_ID in your environment or .env file.")

    creds = None
    if token_file.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
        except Exception as e:
            print(f"⚠️  Error reading token file {token_file}: {e}. Re-authenticating...")

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                print(f"⚠️  Failed to refresh credentials: {e}. Re-running auth flow...")
                creds = None
        
        if not creds:
            if not credentials_file.exists():
                raise FileNotFoundError(
                    f"Google client_secret.json credentials file not found.\n"
                    f"Searched paths:\n"
                    f" - {Path(__file__).resolve().parent / 'tokens/client_secret.json'}\n"
                    f" - {Path(__file__).resolve().parent.parent / 'youtube_api/tokens/client_secret.json'}\n"
                    f"Please verify client_secret.json exists."
                )
            
            # Ensure tokens parent folder exists
            token_file.parent.mkdir(parents=True, exist_ok=True)
            flow = InstalledAppFlow.from_client_secrets_file(str(credentials_file), SCOPES)
            creds = flow.run_local_server(port=0)
            
        with open(token_file, "w") as f:
            f.write(creds.to_json())

    service = build("docs", "v1", credentials=creds)
    print(f"📖 Fetching Google Doc ID: {doc_id}...")
    doc = service.documents().get(documentId=doc_id).execute()
    
    shorts = []
    current_short = None
    
    body_elements = doc.get("body", {}).get("content", [])
    for elem in body_elements:
        if "paragraph" in elem:
            para = elem["paragraph"]
            style = para.get("paragraphStyle", {}).get("namedStyleType", "")
            
            # Extract plain text content
            text = ""
            for part in para.get("elements", []):
                if "textRun" in part:
                    text += part["textRun"].get("content", "")
            
            text_str = text.strip()
            if not text_str:
                continue
                
            if style == "HEADING_2":
                if current_short:
                    shorts.append(current_short)
                current_short = {
                    "title": text_str,
                    "body": ""
                }
            elif style == "NORMAL_TEXT":
                if current_short:
                    if current_short["body"]:
                        current_short["body"] += "\n" + text_str
                    else:
                        current_short["body"] = text_str
                        
    if current_short:
        shorts.append(current_short)
        
    return shorts


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
# Step 3: Title-Based Keyword Fuzzy Matching (Dynamic Programming Alignment)
# ---------------------------------------------------------------------------

def get_title_match_score(title: str, captions, i: int) -> float:
    """Calculates a combined similarity score (fuzzy match score weighted by the
    fraction of title tokens matched) in a sliding window of 1 to 3 captions.
    """
    title_words = set(re.findall(r'\w+', title.lower()))
    if not title_words:
        return 0.0
    
    best_score = 0.0
    for w in range(1, 4):  # windows of size 1, 2, 3
        if i + w <= len(captions):
            combined_text = " ".join(captions[idx][2] for idx in range(i, i + w))
            combined_words = set(re.findall(r'\w+', combined_text.lower()))
            
            # Intersection of words
            intersection = title_words.intersection(combined_words)
            match_ratio = len(intersection) / len(title_words)
            
            # Fuzzy token set ratio
            fuzzy_score = fuzz.token_set_ratio(title, combined_text)
            
            # Calculate fraction of title words in the first caption captions[i]
            first_words = set(re.findall(r'\w+', captions[i][2].lower()))
            first_intersection = title_words.intersection(first_words)
            first_match_ratio = len(first_intersection) / len(title_words)
            
            # Penalty for larger window sizes to prefer tighter windows
            window_penalty = 0.01 * (w - 1)
            
            # Combine metrics
            combined_score = fuzzy_score * match_ratio + 5.0 * first_match_ratio - window_penalty
            if combined_score > best_score:
                best_score = combined_score
    return best_score



def find_short_starts(shorts, captions):
    """Computes the optimal starting caption index for each short such that
    the indices are chronologically ordered (I_0 < I_1 < ... < I_{N-1}) using DP.
    """
    N = len(shorts)
    L = len(captions)
    if N > L or N == 0:
        return None
        
    # Precompute scores: scores[k][i] is the score for matching short k starting at caption index i
    scores = np.zeros((N, L))
    for k in range(N):
        title = shorts[k]["title"]
        for i in range(L):
            scores[k, i] = get_title_match_score(title, captions, i)
            
    # dp[k][i] stores the max score for matching first k shorts, with the k-th short starting at caption i
    dp = np.full((N, L), -1.0)
    parent = np.full((N, L), -1, dtype=int)
    
    # Initialize first short
    for i in range(L - N + 1):
        dp[0, i] = scores[0, i]
        
    # DP transitions
    for k in range(1, N):
        for i in range(k, L - N + k + 1):
            best_prev_score = -1.0
            best_prev_idx = -1
            for j in range(k - 1, i):
                if dp[k-1, j] > best_prev_score:
                    best_prev_score = dp[k-1, j]
                    best_prev_idx = j
            if best_prev_score >= 0:
                dp[k, i] = scores[k, i] + best_prev_score
                parent[k, i] = best_prev_idx
                
    # Find the best ending state
    best_last_idx = -1
    best_total_score = -1.0
    for i in range(N - 1, L):
        if dp[N-1, i] > best_total_score:
            best_total_score = dp[N-1, i]
            best_last_idx = i
            
    if best_last_idx == -1:
        return None
        
    # Reconstruct path
    starts = [0] * N
    curr_idx = best_last_idx
    for k in range(N - 1, -1, -1):
        starts[k] = curr_idx
        curr_idx = parent[k, curr_idx]
        
    return starts, scores


def build_segments_from_starts(captions, starts):
    """Partitions the captions into N segments based on start indices."""
    segments = []
    N = len(starts)
    L = len(captions)
    for k in range(N):
        start_idx = starts[k]
        end_idx = starts[k+1] - 1 if k < N - 1 else L - 1
        
        seg_caps = captions[start_idx : end_idx + 1]
        segments.append({
            "captions": seg_caps,
            "start_time": seg_caps[0][0],
            "end_time": seg_caps[-1][1],
            "text": " ".join(c[2] for c in seg_caps),
            "start_cap_idx": start_idx,
            "end_cap_idx": end_idx
        })
    return segments


# ---------------------------------------------------------------------------
# Step 5 & 6: Overrides, Warning Output & Video Cutting
# ---------------------------------------------------------------------------

def slugify(text: str) -> str:
    """Safely converts text to a lowercase hyphenated slug filename (alphanumeric and hyphens only)."""
    text = text.lower().strip()
    text = re.sub(r'[^a-z0-9\s-]', '', text)
    text = re.sub(r'[\s-]+', '-', text)
    return text.strip('-')


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
    """Prints warning when segment matching fails and segments cannot be extracted."""
    print(f"\n⚠️  [LOW CONFIDENCE] Match for short: '{short['title']}'")
    print(f"   Slug: {slug}")
    print(f"   Confidence score: 0.0 (threshold: {min_confidence})")
    print("   Reason: Title-based fuzzy matching failed or chronological order constraint could not be met.")
    print("   Please use the override flag to supply manually determined timestamps.")
    print("   Script snippet:")
    snippet = short["body"][:200] + "..." if len(short["body"]) > 200 else short["body"]
    print(f"     {snippet}")


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
        "--captions",
        required=True,
        help="Path to the SRT captions file."
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
    
    args = parser.parse_args()
    
    # Resolve paths & validate existence
    video_path = Path(args.video).resolve()
    captions_path = Path(args.captions).resolve()
    
    if not video_path.is_file():
        print(f"❌ Error: Video file does not exist at '{video_path}'")
        sys.exit(1)
    if not captions_path.is_file():
        print(f"❌ Error: Captions file does not exist at '{captions_path}'")
        sys.exit(1)
        
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
        
    # 3. Find Short Start Caption Indices using Keyword Fuzzy Matching
    result = find_short_starts(shorts, captions)
    
    # 4. Build Segments and Matches
    segments = None
    matches = {}
    if result is not None:
        starts, scores = result
        segments = build_segments_from_starts(captions, starts)
        print(f"✂️  Title-based keyword matching segmented video timeline into {len(segments)} segments.")
        for k in range(N):
            matches[k] = {
                "segment_idx": k,
                "confidence": min(100.0, scores[k, starts[k]])
            }
    else:
        print(f"⚠️  Could not match short titles to captions (chronological order constraint could not be met).")

    # 5. Review Matches & Apply Overrides
    cut_jobs = []
    flagged_jobs = []
    
    for idx, short in enumerate(shorts):
        title = short["title"]
        slug = slugify(title)
        
        # Check override
        if slug in overrides:
            start_t, end_t = overrides[slug]
            cut_jobs.append({
                "short": short,
                "slug": slug,
                "start": start_t,
                "end": end_t,
                "source": "Override flag"
            })
        else:
            # Check if auto segment matching is available
            if segments is None or idx not in matches:
                print_no_segment_warning(short, slug, args.min_confidence)
                if args.dry_run:
                    flagged_jobs.append((short, slug, None, 0.0, "No segments detected"))
                else:
                    start_t, end_t = prompt_for_timestamps(title)
                    cut_jobs.append({
                        "short": short,
                        "slug": slug,
                        "start": start_t,
                        "end": end_t,
                        "source": "Manually entered"
                    })
            else:
                match_info = matches[idx]
                seg_idx = match_info["segment_idx"]
                confidence = match_info["confidence"]
                segment = segments[seg_idx]
                
                if confidence >= args.min_confidence:
                    cut_jobs.append({
                        "short": short,
                        "slug": slug,
                        "start": segment["start_time"],
                        "end": segment["end_time"],
                        "source": f"Auto-detected (confidence {confidence:.1f})"
                    })
                else:
                    print_low_confidence_warning(
                        short=short,
                        slug=slug,
                        segment=segment,
                        confidence=confidence,
                        captions=captions,
                        start_idx=segment["start_cap_idx"],
                        end_idx=segment["end_cap_idx"],
                        min_confidence=args.min_confidence
                    )
                    if args.dry_run:
                        flagged_jobs.append((short, slug, segment, confidence, f"Low confidence {confidence:.1f}"))
                    else:
                        start_t, end_t = prompt_for_timestamps(title)
                        cut_jobs.append({
                            "short": short,
                            "slug": slug,
                            "start": start_t,
                            "end": end_t,
                            "source": "Manually entered"
                        })
                    
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

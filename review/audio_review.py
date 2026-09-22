#!/usr/bin/env python3
"""
audio_review.py

Detects unwanted noise events (coughs, throat clearing, sighs, mic thumps, clicks, etc.)
in a video or audio file using PANNs (Cnn14), batched for CPU inference.

Usage:
    python audio_review.py input.mp4
    python audio_review.py input.wav --output flagged.json --threshold 0.15
    python audio_review.py input.mp4 --batch-size 64 --min-duration 0.15
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile

import numpy as np
import librosa
from tqdm import tqdm
from panns_inference import AudioTagging, labels

try:
    from utils import Colors, colorize
except ImportError:
    from review.utils import Colors, colorize

SAMPLE_RATE = 32000  # PANNs was trained at this rate — don't change

DEFAULT_TARGET_CLASSES = [
    # Vocal bloopers & mouth sounds
    "Cough",
    "Throat clearing",
    "Sniff",
    "Sneeze",
    "Sigh",
    "Gasp",
    "Burping, eructation",
    "Hiccup",
    "Laughter",
    "Chuckle, chortle",
    # Desk, mouse & mic impacts
    "Thump, thud",
    "Knock",
    "Tap",
    "Clicking",
    # Room & outside interruptions
    "Door",
    "Ringtone",
    "Bark",
    "Siren",
]


def extract_audio(input_path: str) -> str:
    """
    Extracts mono 32kHz WAV audio from any video/audio file using ffmpeg.
    Returns path to a temp .wav file. Caller is responsible for cleanup.
    """
    fd, wav_path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)

    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-ar", str(SAMPLE_RATE),
        "-ac", "1",
        "-vn",
        wav_path,
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        os.remove(wav_path)
        raise RuntimeError(
            f"ffmpeg failed to extract audio:\n{result.stderr.decode(errors='ignore')}"
        )
    return wav_path


def make_windows(audio: np.ndarray, sr: int, win_sec: float, hop_sec: float):
    """
    Slices audio into fixed-length overlapping windows.
    Returns (windows: np.ndarray of shape [N, win_size], start_times: list[float])
    """
    win_size = int(win_sec * sr)
    hop_size = int(hop_sec * sr)

    windows = []
    start_times = []
    for start in range(0, max(1, len(audio) - win_size), hop_size):
        chunk = audio[start:start + win_size]
        if len(chunk) < win_size:
            chunk = np.pad(chunk, (0, win_size - len(chunk)))
        windows.append(chunk)
        start_times.append(start / sr)

    if not windows:
        chunk = np.pad(audio, (0, win_size - len(audio)))
        windows.append(chunk)
        start_times.append(0.0)

    return np.stack(windows, axis=0), start_times


def run_batched_inference(model, windows: np.ndarray, batch_size: int):
    """
    Runs PANNs inference in batches to keep CPU memory/time reasonable.
    Returns array of shape [N, num_classes] of class probabilities.
    """
    all_probs = []
    n = windows.shape[0]
    with tqdm(total=n, desc="Inference progress", unit="windows", file=sys.stderr) as pbar:
        for i in range(0, n, batch_size):
            batch = windows[i:i + batch_size]
            clipwise_output, _ = model.inference(batch)
            all_probs.append(clipwise_output)
            pbar.update(len(batch))
    return np.concatenate(all_probs, axis=0)


def collect_hits(probs: np.ndarray, start_times: list, target_idx: dict,
                  threshold: float, win_sec: float):
    hits = []
    for row_idx, row in enumerate(probs):
        t = start_times[row_idx]
        for class_name, ci in target_idx.items():
            conf = float(row[ci])
            if conf > threshold:
                hits.append({
                    "time": round(t, 2),
                    "class": class_name,
                    "confidence": round(conf, 3),
                })
    return hits


def merge_hits(hits: list, win_sec: float, gap_thresh: float = 1.0,
               min_duration: float = 0.0):
    """
    Merges overlapping/adjacent hits of the same class into single segments.
    """
    hits = sorted(hits, key=lambda h: (h["class"], h["time"]))
    merged = []
    for h in hits:
        if (merged
                and h["class"] == merged[-1]["class"]
                and h["time"] - merged[-1]["end"] <= gap_thresh):
            merged[-1]["end"] = max(merged[-1]["end"], h["time"] + win_sec)
            merged[-1]["confidence"] = max(merged[-1]["confidence"], h["confidence"])
        else:
            merged.append({
                "start": h["time"],
                "end": h["time"] + win_sec,
                "class": h["class"],
                "confidence": h["confidence"],
            })

    if min_duration > 0:
        merged = [m for m in merged if (m["end"] - m["start"]) >= min_duration]

    merged.sort(key=lambda m: m["start"])
    return merged


def summarize_detected_classes(probs: np.ndarray, start_times: list, top_k: int = 15, min_conf: float = 0.05):
    """
    Finds the highest confidence sound classes detected across all windows in the file.
    """
    max_probs = np.max(probs, axis=0)
    top_indices = np.argsort(max_probs)[::-1]

    summary = []
    for idx in top_indices:
        conf = float(max_probs[idx])
        if conf < min_conf:
            break
        best_win = int(np.argmax(probs[:, idx]))
        peak_time = start_times[best_win]
        summary.append({
            "class": labels[idx],
            "max_confidence": round(conf, 3),
            "peak_time": round(peak_time, 2),
        })
        if top_k and len(summary) >= top_k:
            break
    return summary


def format_timestamp(seconds: float) -> str:
    """Formats float seconds into HH:MM:SS.ss or MM:SS.ss format."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:05.2f}"
    return f"{minutes:02d}:{secs:05.2f}"


def main():
    parser = argparse.ArgumentParser(description="Detect unwanted noise events in a video/audio file.")
    parser.add_argument("input", nargs="?", default=None, help="Path to input video or audio file")
    parser.add_argument("--list-classes", nargs="?", const="", default=None,
                        help="List or search available PANNs/AudioSet labels (optional search filter)")
    parser.add_argument("--output", default=None, help="Optional path to output JSON (default: None, prints to terminal)")
    parser.add_argument("--threshold", type=float, default=0.15, help="Confidence threshold (default: 0.15)")
    parser.add_argument("--win-sec", type=float, default=2.0, help="Window size in seconds (default: 2.0)")
    parser.add_argument("--hop-sec", type=float, default=0.5, help="Hop size in seconds (default: 0.5)")
    parser.add_argument("--batch-size", type=int, default=32, help="Inference batch size (default: 32)")
    parser.add_argument("--gap-thresh", type=float, default=1.0, help="Max gap (sec) to merge adjacent hits of the same class (default: 1.0)")
    parser.add_argument("--min-duration", type=float, default=0.15, help="Drop merged segments shorter than this, in seconds (default: 0.15)")
    parser.add_argument("--classes", nargs="*", default=None,
                         help="Override target class names (case-insensitive search or exact label name)")
    parser.add_argument("--all-classes", action="store_true",
                        help="Flag events for ALL 527 sound classes exceeding threshold (not just target list)")
    parser.add_argument("--top-classes", type=int, default=3,
                        help="Number of top detected sound classes across the entire file to display in summary (default: 3, 0 to disable)")
    args = parser.parse_args()

    if args.list_classes is not None:
        query = args.list_classes.strip().lower()
        matched = [lbl for lbl in labels if query in lbl.lower()] if query else labels
        print(f"Found {len(matched)} matching class(es):")
        for lbl in matched:
            print(f"  - {lbl}")
        return

    if not args.input:
        parser.error("the following arguments are required: input")

    input_path = args.input

    # Resolve class names (case-insensitive lookup)
    if args.all_classes:
        target_classes = labels
    else:
        label_map = {lbl.lower(): lbl for lbl in labels}
        requested_classes = args.classes if args.classes else DEFAULT_TARGET_CLASSES

        target_classes = []
        missing = []
        for c in requested_classes:
            if c in labels:
                target_classes.append(c)
            elif c.lower() in label_map:
                target_classes.append(label_map[c.lower()])
            else:
                missing.append(c)

        if missing:
            print(f"Error: unknown class name(s): {missing}", file=sys.stderr)
            print("Use --list-classes <query> to search available AudioSet labels.", file=sys.stderr)
            sys.exit(1)

    target_idx = {c: labels.index(c) for c in target_classes}

    input_path = os.path.expanduser(input_path)
    ext = os.path.splitext(input_path)[1].lower()
    temp_wav = None
    if ext == ".wav":
        wav_path = input_path
    else:
        print(f"Extracting audio from {input_path} ...", file=sys.stderr)
        temp_wav = extract_audio(input_path)
        wav_path = temp_wav

    try:
        print("Loading audio...", file=sys.stderr)
        audio, sr = librosa.load(wav_path, sr=SAMPLE_RATE, mono=True)

        print("Building windows...", file=sys.stderr)
        windows, start_times = make_windows(audio, sr, args.win_sec, args.hop_sec)
        print(f"{len(windows)} windows to process", file=sys.stderr)

        print("Loading PANNs model (CPU)...", file=sys.stderr)
        model = AudioTagging(checkpoint_path=None, device='cpu')

        print("Running inference...", file=sys.stderr)
        probs = run_batched_inference(model, windows, args.batch_size)

        # Show top detected classes across all 527 AudioSet classes in the entire audio
        if args.top_classes > 0:
            top_summary = summarize_detected_classes(probs, start_times, top_k=args.top_classes)
            print("\n" + colorize("--- Top Detected Sounds in File (Overall) ---", Colors.BOLD + Colors.CYAN))
            for i, item in enumerate(top_summary, 1):
                time_str = format_timestamp(item["peak_time"])
                print(f"  {i:2d}. {item['class']:<30} max conf: {item['max_confidence']:.2f} (peak at {time_str} / {item['peak_time']:.2f}s)")
            print("-" * 50)

        raw_hits = collect_hits(probs, start_times, target_idx, args.threshold, args.win_sec)
        segments = merge_hits(raw_hits, args.win_sec, args.gap_thresh, args.min_duration)

        if args.output:
            with open(args.output, "w") as f:
                json.dump(segments, f, indent=2)
            print(colorize(f"\nSaved {len(segments)} flagged segment(s) to {args.output}", Colors.GREEN), file=sys.stderr)

        print("\n" + "=" * 80)
        print(colorize("AUDIO REVIEW INSPECTION REPORT", Colors.BOLD + Colors.HEADER))
        print("=" * 80)
        print(f"File Path:       {os.path.abspath(input_path)}")
        print(f"Threshold:       {args.threshold}")
        print(f"Target Classes:  {len(target_classes)} class(es) monitored")
        print("-" * 80)

        if not segments:
            print(colorize(f"✅ SUCCESS: No unwanted noise events detected above threshold ({args.threshold})!", Colors.BOLD + Colors.GREEN))
        else:
            print(colorize(f"⚠️  FLAGGED AUDIO EVENTS ({len(segments)}):", Colors.BOLD + Colors.WARNING))
            print()
            for idx, s in enumerate(segments, 1):
                t_start = format_timestamp(s['start'])
                t_end = format_timestamp(s['end'])
                dur = s['end'] - s['start']
                time_range = f"{t_start} --> {t_end}"
                sec_range = f"[{s['start']:.2f}s to {s['end']:.2f}s]"
                print(f"  {idx:2d}. {time_range:<22} {sec_range:<20} Duration: {dur:.2f}s | {s['class']:<20} (conf: {s['confidence']:.2f})")
            print()
            print(colorize("Note: Review these timestamps in your video/audio editor to cut out flagged noise.", Colors.DIM))

        print("=" * 80)

    finally:
        if temp_wav and os.path.exists(temp_wav):
            os.remove(temp_wav)


if __name__ == "__main__":
    main()
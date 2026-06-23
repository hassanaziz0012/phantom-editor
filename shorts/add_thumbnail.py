#!/usr/bin/env python3
"""
Add Thumbnail Prepending Script
Extracts the first frame of a video, overlays the title in the center with Pillow,
and prepends this frame to the video for 0.5s (with audio shifted accordingly to preserve sync).
"""

import os
import sys
import json
import argparse
import tempfile
import subprocess
import textwrap
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

# Add project root to sys.path
repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.append(str(repo_root))

VIDEO_EXTENSIONS = ('.mp4', '.mkv', '.avi', '.mov', '.flv', '.webm', '.wmv', '.m4v', '.mpg', '.mpeg', '.3gp')

def get_video_resolution(video_path):
    """Retrieves the video resolution (width, height) using ffprobe."""
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=s=x:p=0",
            str(video_path)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        out = result.stdout.strip()
        if 'x' in out:
            w, h = out.split('x')
            return int(w), int(h)
    except Exception as e:
        print(f"Warning: Could not determine video resolution using ffprobe: {e}", file=sys.stderr)
    return 1080, 1920

def get_audio_info(video_path):
    """Retrieves whether video has audio, and number of channels using ffprobe."""
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "a:0",
            "-show_entries", "stream=channels",
            "-of", "csv=p=0",
            str(video_path)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        out = result.stdout.strip()
        if out.isdigit():
            return True, int(out)
    except Exception:
        pass
    return False, 0

def find_metadata_for_video(video_path, shorts_json_path):
    """Looks up video metadata in shorts.json using exact path, filename, or stem matching."""
    if not shorts_json_path.exists():
        return None
    try:
        with open(shorts_json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if not isinstance(data, list):
                return None
            
            video_path_obj = Path(video_path).resolve()
            
            # 1. Match exact resolved path
            for entry in data:
                if isinstance(entry, dict) and "video_path" in entry:
                    if Path(entry["video_path"]).resolve() == video_path_obj:
                        return entry
            
            # 2. Match filename
            for entry in data:
                if isinstance(entry, dict) and "video_path" in entry:
                    if Path(entry["video_path"]).name == video_path_obj.name:
                        return entry
                        
            # 3. Match stem
            for entry in data:
                if isinstance(entry, dict) and "video_path" in entry:
                    if Path(entry["video_path"]).dir == video_path_obj.parent and Path(entry["video_path"]).stem == video_path_obj.stem:
                        return entry
                    elif Path(entry["video_path"]).stem == video_path_obj.stem:
                        return entry
    except Exception as e:
        print(f"Warning: Error reading shorts.json: {e}", file=sys.stderr)
    return None

def extract_first_frame(video_path, output_image_path):
    """Extracts the first frame (at 0.0s) of a video file using FFmpeg."""
    cmd = [
        "ffmpeg", "-y",
        "-ss", "00:00:00",
        "-i", str(video_path),
        "-vframes", "1",
        "-q:v", "2",
        str(output_image_path)
    ]
    subprocess.run(cmd, capture_output=True, check=True)

def draw_title_pillow(image_path, title_text, output_path, font_size_override=None):
    """Loads extracted frame and draws centered title in a rounded white background box."""
    img = Image.open(image_path)
    draw = ImageDraw.Draw(img)
    w, h = img.size
    
    # Wrap text to ~15 characters per line
    lines = textwrap.wrap(title_text.upper(), width=15)
    
    # Choose dynamic font size: ~5.5% of video height by default
    font_size = font_size_override if font_size_override else int(h * 0.055)
    
    # Locate a bold sans-serif font
    font = None
    common_fonts = [
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "/usr/share/fonts/truetype/msttcorefonts/Arial_Bold.ttf",
        "arialbd.ttf",
        "arial.ttf"
    ]
    for font_path in common_fonts:
        try:
            font = ImageFont.truetype(font_path, font_size)
            break
        except OSError:
            continue
            
    if not font:
        font = ImageFont.load_default()
        print("Warning: Could not load custom bold font. Using default font.")

    # Calculate individual line dimensions
    line_widths = []
    line_heights = []
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        line_w = bbox[2] - bbox[0]
        line_h = bbox[3] - bbox[1]
        line_widths.append(line_w)
        line_heights.append(line_h)
        
    max_line_width = max(line_widths) if line_widths else 0
    line_gap = font_size * 0.25
    total_text_height = sum(line_heights) + line_gap * (len(lines) - 1)
    
    # White box padding
    pad_x = font_size * 0.7
    pad_y = font_size * 0.45
    box_w = max_line_width + 2 * pad_x
    box_h = total_text_height + 2 * pad_y
    
    # Center bounds
    center_x, center_y = w // 2, h // 2
    box_x0 = center_x - box_w // 2
    box_y0 = center_y - box_h // 2
    box_x1 = center_x + box_w // 2
    box_y1 = center_y + box_h // 2
    
    # Draw white rounded box
    radius = font_size * 0.35
    draw.rounded_rectangle(
        [box_x0, box_y0, box_x1, box_y1],
        radius=radius,
        fill="white"
    )
    
    # Draw text lines
    current_y = box_y0 + pad_y
    for i, line in enumerate(lines):
        line_w = line_widths[i]
        line_x = center_x - line_w // 2
        draw.text((line_x, current_y), line, fill="black", font=font)
        current_y += line_heights[i] + line_gap
        
    img.save(output_path, quality=95)

def prepend_thumbnail(video_path, thumb_path, output_path, duration=0.5):
    """Prepends the static thumbnail image to the video for a set duration, delaying audio to keep sync."""
    width, height = get_video_resolution(video_path)
    has_aud, channels = get_audio_info(video_path)
    
    # Filter graph: Scale both loop image and original video to matching dimension
    filter_complex = (
        f"[0:v]scale={width}:{height},setsar=1[v0]; "
        f"[1:v]scale={width}:{height},setsar=1[v1]; "
        f"[v0][v1]concat=n=2:v=1:a=0[v]"
    )
    
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-t", str(duration), "-i", str(thumb_path),
        "-i", str(video_path)
    ]
    
    if has_aud and channels > 0:
        delay_ms = int(duration * 1000)
        delay_str = "|".join([str(delay_ms)] * channels)
        filter_complex += f"; [1:a]adelay={delay_str}[a]"
        
        cmd.extend([
            "-filter_complex", filter_complex,
            "-map", "[v]",
            "-map", "[a]",
            "-c:v", "libx264",
            "-c:a", "aac",
            "-pix_fmt", "yuv420p",
            str(output_path)
        ])
    else:
        cmd.extend([
            "-filter_complex", filter_complex,
            "-map", "[v]",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            str(output_path)
        ])
        
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg composition failed:\n{result.stderr}")

def process_single_video(video_path, output_path, shorts_json_path, title_override=None, font_size_override=None, duration=0.5):
    """Processes a single video by extracting first frame, drawing title cover, and prepending it."""
    video_path = Path(video_path).resolve()
    
    # 1. Retrieve Title
    title = title_override
    if not title:
        metadata = find_metadata_for_video(video_path, shorts_json_path)
        if metadata and metadata.get("title"):
            title = metadata["title"]
            print(f"Found title in metadata: '{title}'")
        else:
            title = video_path.stem.replace('_', ' ').replace('-', ' ')
            print(f"Warning: No metadata title found for '{video_path.name}'. Falling back to stem: '{title}'")
            
    # 2. Extract first frame & create temporary files
    temp_dir = tempfile.gettempdir()
    extracted_frame = Path(temp_dir) / f"extracted_{video_path.stem}.jpg"
    temp_thumb = Path(temp_dir) / f"thumb_{video_path.stem}.jpg"
    
    try:
        print(f"Extracting first frame from '{video_path.name}'...")
        extract_first_frame(video_path, extracted_frame)
        
        print(f"Overlaying title with Pillow...")
        draw_title_pillow(extracted_frame, title, temp_thumb, font_size_override)
        
        print(f"Prepending cover to video and saving to '{output_path}'...")
        prepend_thumbnail(video_path, temp_thumb, output_path, duration)
        print(f"Success! Saved captioned-thumbnail prepended short to '{output_path}'")
        
    finally:
        # Cleanup temporary files
        if extracted_frame.exists():
            extracted_frame.unlink()
        if temp_thumb.exists():
            temp_thumb.unlink()

def main():
    parser = argparse.ArgumentParser(
        description="Extract the first frame of a short, overlay the title in the center, and prepend it for 0.5s."
    )
    parser.add_argument("video_path", help="Path to the video file or directory (if recursive).")
    parser.add_argument(
        "-R", "--recursive",
        action="store_true",
        help="Process a directory recursively. Input video_path must be a directory."
    )
    parser.add_argument(
        "-o", "--output",
        default=None,
        help="Output file path (only used in single file mode. Defaults to <input_base>_with_thumb.mp4)."
    )
    parser.add_argument(
        "-t", "--title",
        default=None,
        help="Override the title text to burn onto the cover (bypasses shorts.json lookup)."
    )
    parser.add_argument(
        "-f", "--font-size",
        type=int,
        default=None,
        help="Custom font size override for the cover text."
    )
    parser.add_argument(
        "-d", "--duration",
        type=float,
        default=0.5,
        help="Duration in seconds to display the cover frame (default: 0.5s)."
    )
    
    args = parser.parse_args()
    shorts_json_path = repo_root / "shorts" / "shorts.json"
    
    input_path = Path(args.video_path).resolve()
    if not input_path.exists():
        print(f"Error: Path '{args.video_path}' does not exist.", file=sys.stderr)
        sys.exit(1)
        
    if args.recursive:
        if not input_path.is_dir():
            print(f"Error: Path '{args.video_path}' is not a directory, but --recursive was specified.", file=sys.stderr)
            sys.exit(1)
            
        parent_dir = input_path.parent
        output_dir = parent_dir / "thumbnail_prepended"
        output_dir.mkdir(parents=True, exist_ok=True)
        print(f"Recursive mode enabled. Saving outputs to directory: '{output_dir}'")
        
        # Scan recursively
        processed_count = 0
        failed_count = 0
        
        for root, dirs, files in os.walk(input_path):
            # Ignore hidden directories (e.g. .git, __pycache__)
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            
            for file in sorted(files):
                if file.lower().endswith(VIDEO_EXTENSIONS):
                    full_input_file = Path(root) / file
                    
                    # Compute relative subfolder path and target directory
                    rel_path = Path(root).relative_to(input_path)
                    target_subdir = output_dir / rel_path
                    target_subdir.mkdir(parents=True, exist_ok=True)
                    output_file_path = target_subdir / file
                    
                    print(f"\n--- Processing [{processed_count + failed_count + 1}]: {full_input_file.name} ---")
                    try:
                        process_single_video(
                            video_path=full_input_file,
                            output_path=output_file_path,
                            shorts_json_path=shorts_json_path,
                            title_override=args.title,
                            font_size_override=args.font_size,
                            duration=args.duration
                        )
                        processed_count += 1
                    except Exception as e:
                        print(f"Error processing '{file}': {e}", file=sys.stderr)
                        failed_count += 1
                        
        print(f"\nBatch processing finished. Successfully processed: {processed_count}, Failed: {failed_count}")
        if failed_count > 0:
            sys.exit(1)
            
    else:
        if not input_path.is_file():
            print(f"Error: Path '{args.video_path}' is a directory. Use --recursive to process directories.", file=sys.stderr)
            sys.exit(1)
            
        # Determine output file path
        if args.output:
            output_file = Path(args.output).resolve()
        else:
            base, ext = os.path.splitext(str(input_path))
            output_file = Path(f"{base}_with_thumb{ext}")
            
        output_file.parent.mkdir(parents=True, exist_ok=True)
        
        try:
            process_single_video(
                video_path=input_path,
                output_path=output_file,
                shorts_json_path=shorts_json_path,
                title_override=args.title,
                font_size_override=args.font_size,
                duration=args.duration
            )
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

if __name__ == "__main__":
    main()

import os
import re
import shutil
import subprocess
import sys
import textwrap
import uuid
from dataclasses import dataclass
from transcribe import transcribe_video

DEFAULT_FONT = "Google Sans"

@dataclass
class CaptionPreset:
    max_words: int
    font_size: int
    bottom_margin: int
    uppercase: bool = True
    width: int = 20
    font_name: str = DEFAULT_FONT
    animated: bool = False

PRESETS = {
    "shorts": CaptionPreset(max_words=3, font_size=24, bottom_margin=25, uppercase=True, width=14, font_name="League Spartan SemiBold", animated=True),
    "longs": CaptionPreset(max_words=12, font_size=10, bottom_margin=20, uppercase=False, width=60, font_name="Google Sans", animated=False)
}

_WARNED_FONTS = set()

def verify_font(font_name):
    try:
        # Check if the exact name matches fc-list
        list_result = subprocess.run(["fc-list", font_name], capture_output=True, text=True, check=True)
        if list_result.stdout.strip():
            return True, None
        
        # If not, let's get all available font families
        result = subprocess.run(["fc-list", ":", "family"], capture_output=True, text=True, check=True)
        families = set()
        for line in result.stdout.splitlines():
            for family in line.split(","):
                f = family.strip()
                if f:
                    families.add(f.lower())
        
        # Check if the lowercase font_name matches any family
        font_name_lower = font_name.lower()
        if font_name_lower in families:
            return True, None
            
        # Check if the base family name matches any family (stripping style modifiers)
        modifiers = {"regular", "bold", "italic", "semibold", "medium", "light", "thin", "black", "extrabold", "extralight", "oblique"}
        words = font_name.split()
        while words and words[-1].lower() in modifiers:
            words.pop()
        base_name = " ".join(words).lower()
        if base_name in families:
            return True, None
            
        # If still not found, it is not available. Get fallback using fc-match
        match_result = subprocess.run(
            ["fc-match", "-f", "%{family} (%{file})", font_name],
            capture_output=True, text=True, check=True
        )
        fallback = match_result.stdout.strip()
        return False, fallback
    except Exception:
        # If fc-list or fc-match fail/are missing (e.g. non-Linux / no fontconfig)
        return True, None

# Helper functions for ASS caption styling with white rounded backgrounds
def srt_time_to_ass(srt_time_str):
    h_m_s, ms = srt_time_str.split(',')
    h, m, s = h_m_s.split(':')
    return f"{int(h)}:{m}:{s}.{round(int(ms)/10):02d}"

def get_video_resolution(video_path):
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=s=x:p=0",
            video_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        out = result.stdout.strip()
        if 'x' in out:
            w, h = out.split('x')
            return int(w), int(h)
    except Exception as e:
        print(f"Warning: Could not determine video resolution using ffprobe: {e}")
    return 1080, 1920

def convert_srt_to_ass(srt_path, ass_path, video_width, video_height, font_size, bottom_margin, uppercase=False, width=20, font_name="Google Sans", animated=False):
    play_res_y = 384
    play_res_x = int(play_res_y * video_width / video_height)
    
    with open(srt_path, 'r', encoding='utf-8') as f:
        content = f.read()
        
    blocks = re.split(r'\n\s*\n', content.strip().replace('\r\n', '\n'))
    ass_events = []
    
    for block in blocks:
        lines = block.strip().split('\n')
        if len(lines) < 3:
            continue
        
        # Parse times
        time_line = lines[1]
        match = re.match(r'(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,\.]\d{3})', time_line)
        if not match:
            continue
        start_t = srt_time_to_ass(match.group(1))
        end_t = srt_time_to_ass(match.group(2))
        
        text = " ".join(lines[2:]).strip()
        if uppercase:
            text = text.upper()
            
        # Wrap text to width characters per line
        wrapped_text = textwrap.fill(text, width=width)
        lines_list = wrapped_text.split('\n')
        
        # Estimate text dimensions without Pillow
        text_width = max(len(l) for l in lines_list) * (font_size * 0.55)
        text_height = len(lines_list) * (font_size * 1.0)
        
        pad_x = font_size * 0.6
        pad_y = font_size * 0.1
        box_width = text_width + 2 * pad_x
        box_height = text_height + 2 * pad_y
        radius = font_size * 0.35
        
        xl = 0
        xr = round(box_width)
        yt = 0
        yb = round(box_height)
        r = round(radius)
        c = 0.5522847
        
        path = (
            f"m {xl + r} {yt} "
            f"l {xr - r} {yt} "
            f"b {round(xr - r + r*c)} {yt} {xr} {round(yt + r - r*c)} {xr} {yt + r} "
            f"l {xr} {round(yb - r)} "
            f"b {xr} {round(yb - r + r*c)} {round(xr - r + r*c)} {yb} {xr - r} {yb} "
            f"l {xl + r} {yb} "
            f"b {round(xl + r - r*c)} {yb} {xl} {round(yb - r + r*c)} {xl} {yb - r} "
            f"l {xl} {round(yt + r)} "
            f"b {xl} {round(yt + r - r*c)} {round(xl + r - r*c)} {yt} {xl + r} {yt}"
        )
        
        pos_x = play_res_x // 2
        pos_y = play_res_y - bottom_margin - box_height // 2
        
        ass_text = wrapped_text.replace('\n', '\\N')
        
        # Prepare animation override tags if animated=True
        # Scale starts at 95%, scales to 105% in 100ms, then scales to 100% in 100ms
        anim_tags = ""
        if animated:
            anim_tags = "\\fscx95\\fscy95\\t(0,100,\\fscx105\\fscy105)\\t(100,200,\\fscx100\\fscy100)"
            
        box_line = f"Dialogue: 0,{start_t},{end_t},CaptionBox,,0,0,0,,{{\\an5\\pos({pos_x},{pos_y}){anim_tags}\\p1}}{path}{{\\p0}}"
        text_line = f"Dialogue: 1,{start_t},{end_t},CaptionText,,0,0,0,,{{\\an5\\pos({pos_x},{pos_y}){anim_tags}}}{ass_text}"
        
        ass_events.append(box_line)
        ass_events.append(text_line)
        
    header = f"""[Script Info]
Title: Auto Captions
ScriptType: v4.00+
PlayResX: {play_res_x}
PlayResY: {play_res_y}
ScaledBorderAndShadow: Yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: CaptionText,{font_name},{font_size},&H00000000,&H00000000,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,5,10,10,10,1
Style: CaptionBox,{font_name},{font_size},&H00FFFFFF,&H00000000,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,5,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    
    with open(ass_path, 'w', encoding='utf-8') as f:
        f.write(header)
        for line in ass_events:
            f.write(line + '\n')


def generate_captions(video_path, model_path_or_size="medium", max_words=None, output_video_path=None, uppercase=False, font_size=16, preview=False, bottom_margin=10, vad_filter=True, width=20, font_name="Google Sans", animated=False):
    # Verify font availability and prompt user if fallback will be used
    is_available, fallback = verify_font(font_name)
    if not is_available and font_name not in _WARNED_FONTS:
        _WARNED_FONTS.add(font_name)
        print(f"\n[WARNING] Chosen font '{font_name}' is not available on this system.")
        print(f"Fallback font that will be used: {fallback}")
        if sys.stdin.isatty():
            response = input("Do you want to proceed with the fallback font? [Y/n]: ").strip().lower()
            if response not in ('', 'y', 'yes'):
                print("Aborted by user.")
                sys.exit(0)
        else:
            print("Non-interactive terminal detected. Proceeding automatically.")

    # Determine the directory of the input video first to save SRT file in the same folder
    video_dir = os.path.dirname(os.path.abspath(video_path))
    video_name_without_ext, _ = os.path.splitext(os.path.basename(video_path))
    output_srt_path = os.path.join(video_dir, f"{video_name_without_ext}.srt")

    # Determine the final output video path based on the original video path if not explicitly provided
    if not output_video_path:
        base, ext = os.path.splitext(video_path)
        output_video_path = f"{base}_captioned{ext}"

    preview_video_path = None
    if preview:
        print("Preview mode enabled: extracting first 5 seconds of the video...")
        preview_video_path = f"temp_preview_{uuid.uuid4().hex[:8]}.mp4"
        try:
            # Extract first 5 seconds of input video.
            # We re-encode to ensure correct timings and format.
            crop_cmd = [
                "ffmpeg", "-y",
                "-i", video_path,
                "-t", "5",
                preview_video_path
            ]
            subprocess.run(crop_cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            video_path = preview_video_path
        except subprocess.CalledProcessError as e:
            print(f"Error creating preview video: ffmpeg failed with exit code {e.returncode}")
            if os.path.exists(preview_video_path):
                os.remove(preview_video_path)
            raise
        except FileNotFoundError:
            print("Error: ffmpeg is not installed or not found in system PATH. Cannot create preview video.")
            raise

    try:
        if os.path.exists(output_srt_path):
            print(f"Captions file '{output_srt_path}' already exists. Skipping transcription model run.")
        else:
            # Generate the subtitles using the imported transcribe_video function
            transcribe_video(
                video_path=video_path,
                model_path_or_size=model_path_or_size,
                output_srt_path=output_srt_path,
                max_words=max_words,
                uppercase=uppercase,
                preview=False,  # Cropping is already handled above if in preview mode
                vad_filter=vad_filter
            )



        print(f"Burning captions into video and saving to: {output_video_path}")
        
        # Generate temporary ASS file with rounded box formatting
        temp_ass = f"temp_captions_{uuid.uuid4().hex[:8]}.ass"
        
        try:
            # Get video width and height to calculate reference canvas
            v_width, v_height = get_video_resolution(video_path)
            
            # Convert the SRT file to ASS format
            convert_srt_to_ass(
                srt_path=output_srt_path,
                ass_path=temp_ass,
                video_width=v_width,
                video_height=v_height,
                font_size=font_size,
                bottom_margin=bottom_margin,
                uppercase=uppercase,
                width=width,
                font_name=font_name,
                animated=animated
            )
            
            # FFmpeg command to burn the styled ASS subtitles:
            cmd = [
                "ffmpeg", "-y",
                "-i", video_path,
                "-vf", f"subtitles={temp_ass}",
                "-c:v", "libx264",
                "-crf", "18",
                "-preset", "slow",
                "-c:a", "copy",
                output_video_path
            ]
            subprocess.run(cmd, check=True)
            print(f"Success! Captioned video saved to {output_video_path}")
        except subprocess.CalledProcessError as e:
            print(f"Error burning subtitles: ffmpeg failed with exit code {e.returncode}")
            raise
        except FileNotFoundError:
            print("Error: ffmpeg or ffprobe is not installed or not found in system PATH. Cannot burn captions.")
            raise
        finally:
            if os.path.exists(temp_ass):
                os.remove(temp_ass)
    finally:
        if preview_video_path and os.path.exists(preview_video_path):
            os.remove(preview_video_path)

if __name__ == "__main__":
    import argparse

    def positive_int(value):
        ivalue = int(value)
        if ivalue <= 0:
            raise argparse.ArgumentTypeError(f"{value} is an invalid positive int value")
        return ivalue

    def non_negative_int(value):
        ivalue = int(value)
        if ivalue < 0:
            raise argparse.ArgumentTypeError(f"{value} is an invalid non-negative int value")
        return ivalue

    parser = argparse.ArgumentParser(description="Generate captions for a video file.")
    parser.add_argument("video_path", help="Path to the input video file (e.g. mp4).")
    parser.add_argument(
        "--model", "-m",
        choices=["small", "medium", "large"],
        default="medium",
        help="Whisper model size to use locally: small, medium, or large (default: medium)."
    )

    parser.add_argument(
        "--max-words", "-w",
        type=positive_int,
        default=None,
        help="Maximum words per caption segment (for short-form videos like reels/tiktoks)."
    )
    parser.add_argument(
        "--output-video", "-v",
        default=None,
        help="Path to the output video file with burned captions. If not specified, defaults to <input_basename>_captioned{ext}."
    )
    parser.add_argument(
        "--uppercase",
        action="store_true",
        default=None,
        help="Convert captions to uppercase (default: False)."
    )
    parser.add_argument(
        "--no-uppercase",
        dest="uppercase",
        action="store_false",
        help="Disable converting captions to uppercase."
    )
    parser.add_argument(
        "--vad-filter",
        action="store_true",
        default=None,
        help="Use VAD (Voice Activity Detection) filter to ignore silences (default: True)."
    )
    parser.add_argument(
        "--no-vad-filter",
        dest="vad_filter",
        action="store_false",
        help="Disable VAD (Voice Activity Detection) filter."
    )
    parser.add_argument(
        "--font-size", "-f",
        type=positive_int,
        default=None,
        help="Font size for the burned captions (default: 16)."
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Only process the first 5 seconds of the video for preview."
    )
    parser.add_argument(
        "--bottom-margin", "-b",
        type=non_negative_int,
        default=None,
        help="Bottom margin for the burned captions in pixels (default: 10)."
    )
    parser.add_argument(
        "--width",
        type=positive_int,
        default=None,
        help="Maximum line width in characters for text wrapping (default: 20)."
    )
    parser.add_argument(
        "--font", "--font-name",
        dest="font_name",
        default=None,
        help=f"Font name to use for the captions (default: {DEFAULT_FONT})."
    )
    parser.add_argument(
        "--animated",
        action="store_true",
        default=None,
        help="Enable bouncy popup animation for captions (default: False, but True for shorts preset)."
    )
    parser.add_argument(
        "--no-animated",
        dest="animated",
        action="store_false",
        help="Disable bouncy popup animation for captions."
    )
    parser.add_argument(
        "--preset",
        choices=list(PRESETS.keys()),
        help=f"Apply a predefined set of styling options (available presets: {', '.join(PRESETS.keys())})."
    )
    parser.add_argument(
        "--recursive", "-R",
        action="store_true",
        help="Recursively process videos if input path is a folder."
    )

    args = parser.parse_args()

    max_words = args.max_words
    font_size = args.font_size
    bottom_margin = args.bottom_margin
    uppercase = args.uppercase
    vad_filter = args.vad_filter
    width = args.width
    font_name = args.font_name
    animated = args.animated

    if args.preset:
        preset = PRESETS[args.preset]
        if max_words is None:
            max_words = preset.max_words
        if font_size is None:
            font_size = preset.font_size
        if bottom_margin is None:
            bottom_margin = preset.bottom_margin
        if uppercase is None:
            uppercase = preset.uppercase
        if width is None:
            width = preset.width
        if font_name is None:
            font_name = preset.font_name
        if animated is None:
            animated = preset.animated

    if uppercase is None:
        uppercase = False

    if vad_filter is None:
        vad_filter = True

    if animated is None:
        animated = False

    if font_size is None:
        font_size = 16
    if bottom_margin is None:
        bottom_margin = 10
    if width is None:
        width = 20
    if font_name is None:
        font_name = DEFAULT_FONT

    VIDEO_EXTENSIONS = ('.mp4', '.mkv', '.avi', '.mov', '.flv', '.webm', '.wmv', '.m4v', '.mpg', '.mpeg', '.3gp')

    if os.path.isdir(args.video_path):
        if args.output_video is not None:
            parser.error("Cannot specify --output-video when the input is a directory.")

        input_dir = os.path.abspath(args.video_path)
        parent_dir = os.path.dirname(input_dir)
        output_dir = os.path.join(parent_dir, "captioned")

        video_files = []
        if args.recursive:
            for root, dirs, files in os.walk(args.video_path):
                for f in files:
                    if f.lower().endswith(VIDEO_EXTENSIONS):
                        name, _ = os.path.splitext(f)
                        if not name.endswith('_captioned'):
                            video_files.append(os.path.join(root, f))
        else:
            for f in os.listdir(args.video_path):
                full_path = os.path.join(args.video_path, f)
                if os.path.isfile(full_path) and f.lower().endswith(VIDEO_EXTENSIONS):
                    name, _ = os.path.splitext(f)
                    if not name.endswith('_captioned'):
                        video_files.append(full_path)

        if not video_files:
            print(f"No video files found in directory: {args.video_path}")
            exit(0)

        print(f"Found {len(video_files)} video file(s) to process.")
        for idx, video_file in enumerate(sorted(video_files), 1):
            abs_video_file = os.path.abspath(video_file)
            rel_path = os.path.relpath(abs_video_file, start=input_dir)
            output_video_path = os.path.join(output_dir, rel_path)
            os.makedirs(os.path.dirname(output_video_path), exist_ok=True)
            print(f"\n[{idx}/{len(video_files)}] Processing: {video_file}")
            print(f"Target output: {output_video_path}")
            try:
                generate_captions(
                    video_file,
                    model_path_or_size=args.model,
                    max_words=max_words,
                    output_video_path=output_video_path,
                    uppercase=uppercase,
                    font_size=font_size,
                    preview=args.preview,
                    bottom_margin=bottom_margin,
                    vad_filter=vad_filter,
                    width=width,
                    font_name=font_name,
                    animated=animated
                )
            except Exception as e:
                print(f"Error processing {video_file}: {e}")
    else:
        generate_captions(
            args.video_path,
            model_path_or_size=args.model,
            max_words=max_words,
            output_video_path=args.output_video,
            uppercase=uppercase,
            font_size=font_size,
            preview=args.preview,
            bottom_margin=bottom_margin,
            vad_filter=vad_filter,
            width=width,
            font_name=font_name,
            animated=animated
        )
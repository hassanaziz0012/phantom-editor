# Video Editing

This directory contains scripts for automating video editing tasks such as mixing background music and generating/burning auto captions.

## Scripts

### [add_bgm_to_video.sh](../video-editing/add_bgm_to_video.sh)
Mixes a background music (BGM) track into a video's audio track. Loops the music if it is shorter than the video, adjusts BGM volume, and outputs a new video file while performing a lossless video stream copy.
* **Usage**: `./video-editing/add_bgm_to_video.sh <path_to_video> <bgm_track> [--volume <percentage>]`
* **Default Output**: `<video_name>-bgm.mp4` in the same directory as the source video.
* **BGM Library**: Looks up tracks relative to `/mnt/c/Users/hassa/Videos/Asset Library/BGM` if a full path is not provided.

### [auto_caption.py](../video-editing/auto_caption.py)
Transcribes audio from a video using the Faster Whisper model, generates a subtitles `.srt` file, and optionally burns the captions directly into the video using `ffmpeg` with custom styling or presets (e.g. for shorts).
* **CLI Command**: `phantom edit caption <video_path> [arguments]`
* **Usage/Arguments**:
  - `-h`, `--help`: Show the help message and exit.
  - `-m MODEL`, `--model MODEL`: Path to local model directory or Hugging Face model size (default: `video-editing/models/faster-whisper-small.en`).
  - `-o OUTPUT`, `--output OUTPUT`: Path to the output SRT file (default: `captions.srt`).
  - `-w MAX_WORDS`, `--max-words MAX_WORDS`: Maximum words per caption segment.
  - `-v OUTPUT_VIDEO`, `--output-video OUTPUT_VIDEO`: Path to the output video file with burned captions.
  - `--srt-only`: Only generate the SRT subtitle file and skip burning it into the video.
  - `--no-uppercase`: Disable converting captions to uppercase.
  - `-f FONT_SIZE`, `--font-size FONT_SIZE`: Font size for the burned captions.
  - `--preview`: Only process the first 5 seconds of the video for preview.
  - `-b BOTTOM_MARGIN`, `--bottom-margin BOTTOM_MARGIN`: Bottom margin for the burned captions in pixels.
  - `--preset {shorts}`: Apply a predefined set of styling options.

### [attach_webcam_mask.py](../video-editing/attach_webcam_mask.py)
Overlays a webcam video recording in the top-right corner of screen footage with rounded corners.
* **CLI Command**: `phantom edit attach-webcam-mask --screen <screen_video> --webcam <webcam_video> [arguments]`
* **Usage/Arguments**:
  - `--screen`: Path to the screen recording video file.
  - `--webcam`: Path to the webcam recording video file.
  - `-o`, `--output`: Path to save the output video file (default: `[screen_basename]_webcam.mp4` in screen folder).
  - `-w`, `--width`: Width of the webcam overlay in pixels (default: `400`).
  - `-r`, `--radius`: Corner radius for the webcam overlay rounded rectangle in pixels (default: `20`).
  - `-d`, `--offset`: Margin/offset from the top-right corner in pixels (default: `20`).
* **Requirements**: `ffmpeg` and `ffprobe` installed on system.
* **Key Features**:
  - Automatically pads the screen video using `tpad` to loop/clone the last frame indefinitely if it is shorter than the webcam recording.
  - Trims the output video exactly to the duration of the webcam video.
  - Uses webcam audio track if available; falls back to screen audio track if webcam has no audio; writes a video-only output if neither has audio.
  - Performs mathematically rounded corner masking on the fly using FFmpeg's `split`, `geq`, `format=gray`, and `alphamerge` filter graphs.

### [transcribe.py](../video-editing/transcribe.py)
Transcribes audio from a video using local Faster Whisper models and outputs a standardized subtitle `.srt` file.
* **CLI Command**: `phantom edit transcribe <video_path> [arguments]`
* **Usage/Arguments**:
  - `--model`, `-m`: Whisper model size to use locally: `small`, `medium`, or `large` (default: `medium`, mapped to directories in `video-editing/models/`).
  - `--max-words`, `-w`: Maximum words per caption segment (for short-form video formats).
  - `--uppercase` / `--no-uppercase`: Convert captions to uppercase (default: `False`).
  - `--vad-filter` / `--no-vad-filter`: Use VAD (Voice Activity Detection) filter to ignore silences (default: `True`).
  - `--preview`: Only process the first 5 seconds of the video for a fast preview test.
  - `--output`, `-o`: Path to save the generated subtitles `.srt` file (default: same folder and basename as input video file with `.srt` extension).
* **Key Features**:
  - Automatically maps standard Whisper model size strings to local directory models under `video-editing/models/` (e.g. `faster-whisper-small.en`).
  - Runs model inference on CPU using `int8` quantization for optimal execution speed.

### [trim_silences.py](../video-editing/trim_silences.py)
Trims silences from a video using speech/caption intervals generated via Whisper.
* **CLI Command**: `phantom edit trim-silences <video_path> [arguments]`
* **Usage/Arguments**:
  - `-o`, `--output`: Path to save the trimmed output video (default: `trimmed_output.mp4` in input video's directory). Cannot be used with `--recursive`.
  - `-R`, `--recursive`: Process a folder recursively, searching for and trimming all videos (outputs saved to a parallel `trimmed/` directory in the parent folder).
  - `-m`, `--model`: Whisper model size to use locally: `small`, `medium`, or `large` (default: `medium`).
  - `--padding`: Padding in seconds to add to the start and end of each speech interval to keep transitions natural (default: `0.15`s).
  - `--min-silence`: Minimum silence duration in seconds to split segments. Gaps smaller than this are merged to maintain flow (default: `0.4`s).
* **Key Features**:
  - If a word-level subtitle file (`<video_name>-1word.srt`) does not exist, it automatically invokes `transcribe_video` with `max_words=1` to generate it.
  - Merges close speech blocks and cuts the video in a single-pass using FFmpeg's `select`/`aselect` filtergraph, ensuring audio-video sync is preserved.

### [utils.py](../video-editing/utils.py)
A shared utility module containing helper functions for timestamp parsing/formatting, slug generation, and Google Docs retrieval:
- `format_srt_time(seconds)`: Converts float seconds to `HH:MM:SS,mmm` string format.
- `parse_timestamp(val)`: Parses float seconds or `HH:MM:SS,mmm` strings into float seconds.
- `slugify(text)`: Converts arbitrary text to a lowercase hyphenated filename-safe slug.
- `get_google_doc_shorts(...)`: Connects to the Google Docs API using OAuth2 and parses headers (as titles) and normal text paragraphs (as bodies) for script metadata.



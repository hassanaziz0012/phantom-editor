# Video Editing

This directory contains scripts for automating video editing tasks such as mixing background music and generating/burning auto captions.

## Scripts

### [add_bgm_to_video.sh](../video-editing/add_bgm_to_video.sh)
Mixes a background music (BGM) track into a video's audio track. Loops the music if it is shorter than the video, adjusts BGM volume, and outputs a new video file while performing a lossless video stream copy.

* **Usage**: `./video-editing/add_bgm_to_video.sh <path_to_video_or_folder> <bgm_track> [--volume <percentage>] [-R|--recursive]`
* **Default Output**: `<video_name>-bgm.mp4` in the same directory as the source video for single file mode. For directory mode, outputs are saved under a parallel `added_bgm/` directory next to the folder's parent directory, preserving the original folder structure.
* **BGM Library**: Looks up tracks relative to `/mnt/c/Users/hassa/Videos/Asset Library/BGM` if a full path is not provided.
* **Usage/Arguments**:
  - `--volume <percentage>`: Set the BGM volume (1-100). Default is `10`%.
  - `-R`, `--recursive`: Recursively process videos if the input path is a folder. If not specified, folder mode only processes top-level videos.

### [auto_caption.py](../video-editing/auto_caption.py)
Transcribes audio from a video using the Faster Whisper model, generates a subtitles `.srt` file, and burns the captions directly into the video using `ffmpeg` with custom styling or presets (e.g. for shorts and longs).

* **CLI Command**: `phantom edit caption <video_path> [arguments]`
* **Usage/Arguments**:
  - `-h`, `--help`: Show the help message and exit.
  - `-m MODEL`, `--model MODEL`: Whisper model size/path locally (default: `medium`, mapped to directories in `video-editing/models/`).
  - `-w MAX_WORDS`, `--max-words MAX_WORDS`: Maximum words per caption segment.
  - `-v OUTPUT_VIDEO`, `--output-video OUTPUT_VIDEO`: Path to the output video file with burned captions (default: `<input_basename>_captioned{ext}`). Cannot be specified in directory mode.
  - `-c CAPTIONS`, `--captions CAPTIONS`: Path to an existing SRT captions file to use instead of generating them. Cannot be specified in directory mode.
  - `--uppercase` / `--no-uppercase`: Convert captions to uppercase (default: `False`, but `True` in `shorts` preset).
  - `--vad-filter` / `--no-vad-filter`: Use VAD (Voice Activity Detection) filter to ignore silences (default: `True`).
  - `-f FONT_SIZE`, `--font-size FONT_SIZE`: Font size for the burned captions (default: `16`).
  - `--preview`: Only process the first 5 seconds of the video for a fast preview test.
  - `-b BOTTOM_MARGIN`, `--bottom-margin BOTTOM_MARGIN`: Bottom margin for the burned captions in pixels (default: `10`).
  - `--width WIDTH`: Maximum line width in characters for text wrapping (default: `20`).
  - `--font`, `--font-name FONT_NAME`: Font family name to use for the captions (default: `Google Sans`).
  - `--animated` / `--no-animated`: Enable or disable bouncy popup animation for captions (default: `False`, but `True` in `shorts` preset).
  - `--preset {shorts, longs}`: Apply a predefined set of styling options.
  - `-R`, `--recursive`: Recursively process videos if the input path is a directory.

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

### [auto_attach_webcam_mask.py](../video-editing/auto_attach_webcam_mask.py)
Overlays a webcam video recording in the top-right corner of screen footage with rounded corners dynamically, toggling the overlay on and off based on voice commands in the audio (`webcam start`/`webcam stop`).

* **CLI Command**: `phantom edit auto-attach-webcam --screen <screen_video> --webcam <webcam_video> [arguments]`
* **Usage/Arguments**:
  - `--screen`: Path to the screen recording video file.
  - `--webcam`: Path to the webcam recording video file.
  - `-o`, `--output`: Path to save the output video file (default: `[screen_basename]_auto_webcam.mp4` in screen folder).
  - `-w`, `--width`: Width of the webcam overlay in pixels (default: `400`).
  - `-r`, `--radius`: Corner radius for the webcam overlay rounded rectangle in pixels (default: `20`).
  - `-d`, `--offset`: Margin/offset from the top-right corner in pixels (default: `20`).
  - `-m`, `--model`: Whisper model size to use locally for transcription (`small`, `medium`, `large`; default: `medium`).
  - `--captions`: Path to save or reuse the intermediate one-word captions file (default: `[webcam_basename]_1word.srt`).
  - `--default-overlay`: Start the video in overlay mode (default: `False`, meaning it starts full-screen raw webcam).
  - `--force-reencode`: (Deprecated) Force re-encoding of all video segments. Transcoding is now always performed in a single pass.
* **Requirements**: `ffmpeg` and `ffprobe` installed on system.
* **Key Features**:
  - Transcribes the webcam audio using Faster Whisper with word-level timestamps (`max_words=1`).
  - Parses the SRT file using a state machine to resolve overlay ranges based on voice triggers: "webcam start"/"stop" or "web cam start"/"stop".
  - Generates the overlay timeline and constructs a dynamic, single-pass FFmpeg filter complex.
  - Switches between full-screen webcam and corner-overlay webcam seamlessly on a single video timeline without splitting or concatenating files.
  - Pre-generates a static rounded corner mask image using FFmpeg to avoid running the slow `geq` filter per-frame, substantially optimizing render times.
  - Applies this static mask to the corner-overlay webcam segments on the fly.

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

### [transcribe_cloud.py](../video-editing/transcribe_cloud.py)
Transcribes audio from a video using Groq Cloud's Whisper API and outputs a standardized subtitle `.srt` file.

* **CLI Command**: `phantom edit transcribe-cloud <video_path> [arguments]`
* **Usage/Arguments**:
  - `--model`, `-m`: Whisper model to use on Groq Cloud: `whisper-large-v3` or `whisper-large-v3-turbo` (default: `whisper-large-v3`).
  - `--max-words`, `-w`: Maximum words per caption segment (for short-form video formats).
  - `--uppercase` / `--no-uppercase`: Convert captions to uppercase (default: `False`).
  - `--preview`: Only process the first 5 seconds of the video for a fast preview test.
  - `--output`, `-o`: Path to save the generated subtitles `.srt` file (default: same folder and basename as input video file with `.srt` extension).
* **Key Features**:
  - Automatically handles the 25 MB file size limit of Groq Cloud by compressing audio or segmenting/chunking it into 10-minute blocks using `ffmpeg`.
  - Runs transcription in the cloud for near-instant speeds compared to local execution.
  - Processes word-level timestamps returned from Groq's top-level `"words"` response array and always saves a companion 1-word-per-timestamp subtitle file named `{output_file}-1word.srt` in the same directory.

### [trim_silences.py](../video-editing/trim_silences.py)
Trims silences from a video using Silero VAD (Voice Activity Detection) via `faster_whisper.vad` to detect speech.

* **CLI Command**: `phantom edit trim-silences <video_path> [arguments]`
* **Usage/Arguments**:
  - `-o`, `--output`: Path to save the trimmed output video (default: `trimmed_output.mp4` in input video's directory). Cannot be used with `--recursive`.
  - `-R`, `--recursive`: Process a folder recursively, searching for and trimming all videos (outputs saved to a parallel `trimmed/` directory in the parent folder).
  - `--padding`: Padding in seconds to add to the start and end of each speech interval to keep transitions natural (default: `0.15`s).
  - `--min-silence`: Minimum silence duration in seconds to split segments. Gaps smaller than this are merged to maintain flow (default: `0.4`s).
  - `--threshold`: Speech threshold. Probabilities above this value are considered speech (default: `0.5`).
* **Key Features**:
  - Decodes mono audio at 16kHz directly from the video without transcribing to text first.
  - Automatically loads and runs the pre-trained Silero VAD ONNX model using ONNX Runtime.
  - Applies a single-pass jump-cut trim using FFmpeg's `select`/`aselect` filtergraph, ensuring high performance and audio-video sync preservation.

### [trim_silences_whisper.py](../video-editing/trim_silences_whisper.py)
> [!WARNING]
> This script is **deprecated** and will be removed in a future release. Please use the Silero VAD-based [trim_silences.py](../video-editing/trim_silences.py) instead.

Trims silences from a video using speech/caption intervals generated via Whisper.

* **CLI Command**: `phantom edit trim-silences-whisper <video_path> [arguments]`
* **Usage/Arguments**:
  - `-o`, `--output`: Path to save the trimmed output video (default: `trimmed_output.mp4` in input video's directory). Cannot be used with `--recursive`.
  - `-R`, `--recursive`: Process a folder recursively, searching for and trimming all videos (outputs saved to a parallel `trimmed/` directory in the parent folder).
  - `-m`, `--model`: Whisper model size to use locally: `small`, `medium`, or `large` (default: `medium`).
  - `--padding`: Padding in seconds to add to the start and end of each speech interval to keep transitions natural (default: `0.15`s).
  - `--min-silence`: Minimum silence duration in seconds to split segments. Gaps smaller than this are merged to maintain flow (default: `0.4`s).
  - `-c`, `--captions`: Path to a custom SRT captions file. A 1-word timestamp caption file format is REQUIRED. If not specified, the script looks for or generates a local 1-word SRT file. Cannot be used with `--recursive`.
* **Key Features**:
  - If a custom captions file is not provided via `--captions` and a word-level subtitle file (`<video_name>-1word.srt` or legacy `captions_1word.srt`) does not exist, it automatically invokes `transcribe_video` with `max_words=1` to generate it.
  - Merges close speech blocks and cuts the video in a single-pass using FFmpeg's `select`/`aselect` filtergraph, ensuring audio-video sync is preserved.

### [utils.py](../video-editing/utils.py)
A shared utility module containing helper functions for timestamp parsing/formatting, slug generation, and Google Docs retrieval:

- `format_srt_time(seconds)`: Converts float seconds to `HH:MM:SS,mmm` string format.
- `parse_timestamp(val)`: Parses float seconds or `HH:MM:SS,mmm` strings into float seconds.
- `slugify(text)`: Converts arbitrary text to a lowercase hyphenated filename-safe slug.
- `get_google_doc_shorts(...)`: Connects to the Google Docs API using OAuth2 and parses headers (as titles) and normal text paragraphs (as bodies) for script metadata.



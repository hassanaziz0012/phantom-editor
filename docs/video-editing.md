# Video Editing

This directory contains scripts for automating video editing tasks such as mixing background music and generating/burning auto captions.

## Scripts

### [add_bgm_to_video.sh](../video-editing/add_bgm_to_video.sh)
Mixes a background music (BGM) track into a video's audio track. Loops the music if it is shorter than the video, adjusts BGM volume, and outputs a new video file while performing a lossless video stream copy.

* **CLI Command**: `phantom edit add-bgm <path_to_video_or_folder> <bgm_track> [arguments]`
* **Usage**: `./video-editing/add_bgm_to_video.sh <path_to_video_or_folder> <bgm_track> [--volume <percentage>] [-R|--recursive]`
* **Default Output**: `<video_name>-bgm.mp4` in the same directory as the source video for single file mode. For directory mode, outputs are saved under a parallel `added_bgm/` directory next to the folder's parent directory, preserving the original folder structure.
* **BGM Library**: Looks up tracks relative to `~/Videos/Asset Library/BGM` if a full path is not provided.
* **Usage/Arguments**:
  - `--volume <percentage>`: Set the BGM volume (1-100). Default is `10`%.
  - `-R`, `--recursive`: Recursively process videos if the input path is a folder. If not specified, folder mode only processes top-level videos.

### [auto_caption.py](../video-editing/auto_caption.py)
Transcribes audio from a video with Groq Cloud's Whisper API (via `transcribe_cloud.py`), generates a subtitles `.srt` file, and burns the captions directly into the video using `ffmpeg` with custom styling or presets (e.g. for shorts and longs).

* **CLI Command**: `phantom edit caption <video_path> [arguments]`
* **Usage/Arguments**:
  - `-h`, `--help`: Show the help message and exit.
  - `-m MODEL`, `--model MODEL`: Whisper model to use on Groq Cloud: `whisper-large-v3` or `whisper-large-v3-turbo` (default: `whisper-large-v3`). The old local sizes (`small`, `medium`, `large`) are still accepted and use the default.
  - `-w MAX_WORDS`, `--max-words MAX_WORDS`: Maximum words per caption segment.
  - `-v OUTPUT_VIDEO`, `--output-video OUTPUT_VIDEO`: Path to the output video file with burned captions (default: `<input_basename>_captioned{ext}`). Cannot be specified in directory mode.
  - `-c CAPTIONS`, `--captions CAPTIONS`: Path to an existing SRT captions file to use instead of generating them. Cannot be specified in directory mode.
  - `--uppercase` / `--no-uppercase`: Convert captions to uppercase (default: `False`, but `True` in `shorts` preset).
  - `-f FONT_SIZE`, `--font-size FONT_SIZE`: Font size for the burned captions (default: `16`).
  - `--preview`: Only process the first 5 seconds of the video for a fast preview test.
  - `-b BOTTOM_MARGIN`, `--bottom-margin BOTTOM_MARGIN`: Bottom margin for the burned captions in pixels (default: `10`).
  - `--width WIDTH`: Maximum line width in characters for text wrapping (default: `20`).
  - `--font`, `--font-name FONT_NAME`: Font family name to use for the captions (default: `Google Sans`).
  - `--animated` / `--no-animated`: Enable or disable bouncy popup animation for captions (default: `False`, but `True` in `shorts` preset).
  - `--preset {shorts, longs}`: Apply a predefined set of styling options.
  - `-R`, `--recursive`: Recursively process videos if the input path is a directory.

### [downscale.py](../video-editing/downscale.py)
Downscales a video to 1080p resolution using FFmpeg with bicubic scaling.

* **CLI Command**: `phantom edit downscale <video_path> [arguments]`
* **Usage/Arguments**:
  - `video_path`: Path to the input video file.
  - `-o`, `--output`: Path to save the downscaled output video (default: `<input_basename>-1080.<ext>` in the input file's directory).
* **Key Features**:
  - Uses FFmpeg's `scale=1920:1080:flags=bicubic` filter along with `libx264` (`-preset veryfast`, `-crf 20`) and copies audio without re-encoding (`-c:a copy`).

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

### [generate_timestamps.py](../metadata/generate_timestamps.py)
Generates YouTube chapters and video topics with start timestamps from a phrase-level `.srt` captions file using Claude via BrowserLLM, and updates the project's `metadata.json`.

* **CLI Command**: `phantom meta timestamps [<captions_srt_path>] [--metadata <path>]`
* **Usage/Arguments**:
  - `captions`: Path to the phrase-level `.srt` captions subtitle file, video file, or project directory (phrase-level captions, not 1-word).
  - `-m`, `--metadata`: Path to a custom `metadata.json` (default: `metadata.json` in the project directory).
* **Key Features**:
  - Formats phrase-level captions with timestamp cues into a clean transcript.
  - Constructs prompt using [`agentic/prompts/generate_timestamps.md`](../agentic/prompts/generate_timestamps.md).
  - Queries Claude through BrowserLLM and parses the generated JSON schema.
  - Ensures the first chapter starts at `00:00` for YouTube chapter compatibility.
  - Updates the `"timestamps"` array directly in the project's existing `metadata.json`.

### [utils.py](../video-editing/utils.py)
A shared utility module containing helper functions for subtitle/SRT parsing, timestamp formatting, slug generation, and Google Docs retrieval:

- `parse_srt(srt_path)`: Parses an SRT subtitle file into a list of `(start_seconds, end_seconds, text)` tuples.
- `parse_srt_to_text(srt_path)`: Parses an SRT subtitle file into clean continuous plain text.
- `parse_srt_to_timestamped_transcript(srt_path)`: Parses an SRT subtitle file into a formatted transcript with `[MM:SS]` or `[HH:MM:SS]` cues.
- `format_srt_time(seconds)`: Converts float seconds to `HH:MM:SS,mmm` string format.
- `parse_timestamp(val)`: Parses float seconds or `HH:MM:SS,mmm` strings into float seconds.
- `slugify(text)`: Converts arbitrary text to a lowercase hyphenated filename-safe slug.
- `get_google_doc_shorts(...)`: Connects to the Google Docs API using OAuth2 and parses headers (as titles) and normal text paragraphs (as bodies) for script metadata.



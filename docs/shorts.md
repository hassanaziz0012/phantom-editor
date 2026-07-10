# Shorts Pipeline

This directory contains the core pipeline scripts for managing, cutting, prepping, and uploading vertical short-form videos (YouTube Shorts, Instagram Reels, and TikToks).

## Database: [shorts.json](../shorts/shorts.json)
The pipeline is driven by a unified metadata database `shorts.json` stored in the `shorts/` directory. Each item in the database has the following schema:

```json
{
  "video_path": "/absolute/path/to/video.mp4",
  "title": "Short Title",
  "description": "Short description text",
  "thumbnail": "relative/or/absolute/path/to/thumbnail.png",
  "tags": ["tag1", "tag2"],
  "scheduled_time": "YYYY-MM-DD HH:MM",
  "added_at": "ISO-8601-Timestamp",
  "posted": {
    "youtube": false,
    "instagram": false,
    "tiktok": false
  }
}

```

## Scripts

### [process_shorts.py](../shorts/process_shorts.py)
Unified Shorts Production Pipeline. It orchestrates the entire flow from a raw video file to fully processed, captioned, and cataloged shorts.

* **Usage**: `python shorts/process_shorts.py <raw_video> --bgm-track <track> [arguments]`
* **Arguments**:
  - `raw_video`: Path to the raw input video file.
  - `--bgm-track`: Background music track name or full path to mix in.
  - `--bgm-volume`: Volume percentage (1-100) for BGM (default: `10`).
  - `--output-dir`, `-O`: Custom workspace path. Defaults to a folder named after the video stem under the video's parent.
  - `--skip-silence`: Skip silence-trimming (Step 1).
  - `--silence-model`: Whisper model size for silence detection (`small`, `medium`, `large`; default: `medium`).
  - `--silence-padding`: Padding in seconds for speech intervals (default: `0.15`s).
  - `--silence-min`: Minimum silence duration to split segments (default: `0.4`s).
  - `--trim-padding`: Pre/post-roll buffer for cut clips (default: `0.5`s).
  - `--min-confidence`: Fuzzy-match confidence threshold (default: `70.0`).
  - `--override`: Repeatable override flag (`slug=START,END`).
  - `--dry-run`: Preview cut plan without cutting clips (presents interactive prompt to proceed, override, run manually, or abort).
  - `--manual`: Run clip cutting in interactive manual mode.
  - `--thumb-font-size`: Font size override for the cover text.
  - `--thumb-duration`: Duration in seconds to display cover frame (default: `0.25`s).
  - `--caption-model`: Whisper model size for captions (default: `medium`).
  - `--caption-max-words`: Max words per caption segment.
  - `--caption-font-size`: Font size for burned captions.
  - `--caption-bottom-margin`: Bottom margin for captions in pixels.
  - `--caption-width`: Max line width in characters for text wrapping (default: `20`).
  - `--caption-preset`: Predefined caption styling preset (e.g. `shorts`).
  - `--caption-font`: Font family name to use for captions.
  - `--caption-uppercase` / `--caption-no-uppercase`: Force or disable uppercase captions.
  - `--caption-vad-filter` / `--caption-no-vad-filter`: Enable or disable VAD filtering.
  - `--shorts-json`: Path to custom `shorts.json` file.
* **Workflow Steps**:
  1. **Trim Silences**: Automatically runs `trim_silences.py` on the raw video to remove dead space.
  2. **Trim Clips**: Runs `auto_trim_shorts.py` to match scripts against Whisper transcripts and cut matching segments.
  3. **Process Audio**: Runs `process_audio.sh` recursively on cut clips to clean and normalize vocal audio.
  4. **Add BGM**: Runs `add_bgm_to_video.sh` recursively to mix in the specified background music.
  5. **Manual Review**: Pauses execution and prompts the user to verify the cuts in the workspace directory.
  6. **Prepend Thumbnails**: Runs `add_thumbnail.py` recursively to generate and burn styled title cover frames.
  7. **Auto Captioning**: Runs `auto_caption.py` recursively to transcribe and overlay bouncy styled subtitles.
  8. **Catalog Metadata**: Runs `create_bulk_metadata.py` recursively to catalog all created shorts in the metadata database.

### [upload.py](../shorts/upload.py)
Unified entry-point script to orchestrate uploading a short video to one or all platforms.

* **CLI Command**: `phantom shorts upload --platform <youtube|instagram|tiktok|all> <video_path>`
* **Usage**: `python shorts/upload.py <video_path> --platform <youtube|instagram|tiktok|all>`
* **Key Features**:
  - Validates that the target video file exists.
  - If the video's metadata is missing from `shorts.json`, the script prompts the user to interactively enter the title, description, tags, thumbnail, and scheduled time, cataloging it automatically before proceeding.
  - Automatically routes execution to the platform-specific scripts (`youtube_api/upload_short.py`, `instagram/upload_reel.py`, and `tiktok/upload_tiktok.py`).
  - Tracks success/failure across platforms and prints a clean completion summary.

### [create_bulk_metadata.py](../shorts/create_bulk_metadata.py)
Interactively scans a folder of MP4 files, filters out videos already in `shorts.json`, and guides the user through typing metadata for each new video.

* **CLI Command**: `phantom shorts metadata create <folder_path> [arguments]`
* **Usage**: `python shorts/create_bulk_metadata.py <folder_path> [--shorts-json <custom_json_path>]`
* **Key Features**:
  - Automatically runs `clean_metadata` first (prompting to clean fully-posted items).
  - Prompts the user for: Title, Description, Thumbnail, Tags (comma-separated), and Scheduled Time.
  - Saves progress immediately after each video to prevent loss of inputs.
  - Allows skipping a video by leaving title blank or typing `s`/`skip`, and global exit by typing `q`/`quit`.

### [clean_metadata.py](../shorts/clean_metadata.py)
Utility to remove fully posted items from the database.

* **CLI Command**: `phantom shorts metadata clean`
* **Usage**: `python shorts/clean_metadata.py`
* **Key Features**:
  - Detects items in `shorts.json` that have been posted to all three platforms (YouTube, Instagram, TikTok).
  - Backs up the current database to `shorts.json.bak`.
  - Clears fully posted entries and saves the remaining ones back.

### [add_thumbnail.py](../shorts/add_thumbnail.py)
Extracts the first frame of a short video, overlays a stylized text box in the center containing the short's title, and prepends this cover frame to the video for a split-second. This ensures that browsers/players display the title cover as the video's default poster frame.

* **CLI Command**: `phantom shorts add-thumbnail [arguments] <video_path>`
* **Usage/Arguments**:
  - `video_path`: Path to video file or directory (if recursive).
  - `-R`, `--recursive`: Process a directory recursively, writing files to a parallel `thumbnail_prepended/` directory.
  - `-o`, `--output`: Output file path (single file mode only).
  - `-t`, `--title`: Override the title text to burn (bypasses `shorts.json` lookup).
  - `-f`, `--font-size`: Custom font size override.
  - `-d`, `--duration`: Duration in seconds to display the cover frame (default: `0.25`s).
* **Key Features**:
  - Resolves the title by searching `shorts.json` (matching exact path, filename, or stem); falls back to the video stem if not found.
  - Overlays the title in a rounded white background box with black uppercase text using Pillow (safely looking up common bold system fonts).
  - Concatenates the static frame to the video using FFmpeg.
  - To prevent video-audio de-sync, it automatically shifts/delays the audio stream using FFmpeg's `adelay` filter graph by the exact duration of the prepended cover.

### [auto_trim_shorts.py](../shorts/auto_trim_shorts.py)
Automates cutting long raw video files into individual shorts by matching spoken speech (Whisper transcripts) with scripts outlined in a Google Doc.

* **CLI Command**: `phantom shorts trim [arguments]`
* **Usage/Arguments**:
  - `--video`: Path to the raw long-form video file.
  - `--padding`: Seconds of pre/post-roll buffer added to each cut (default: `0.5`s).
  - `--min-confidence`: Fuzzy-match confidence score threshold (0-100) (default: `70.0`).
  - `--override`: Bypass detection for a short. Format: `slug=START,END` (repeatable).
  - `--dry-run`: Compute and print the match/cut plan without invoking FFmpeg or writing files.
  - `--manual`: Interactive manual mode that prompts the user for start times of each short.
* **Environment Configuration**:
  - `SHORTS_GOOGLE_DOC_ID`: The ID of the Google Doc outlining your short scripts. The document structure should have each short script starting with a `HEADING_2` title, followed by `NORMAL_TEXT` paragraphs containing the body/script.
* **Workflow**:
  - Fetches the short scripts outline from Google Docs.
  - Transcribes the video using local Faster Whisper (generating a word-level SRT if it doesn't already exist).
  - Computes natural silence gaps between speech segments to identify candidate boundaries.
  - Formulates a bipartite matching problem and solves it using linear sum assignment (via `scipy.optimize.linear_sum_assignment` and fuzzy ratios from `rapidfuzz`) to pair scripts to video segments.
  - Refines start times by scanning for the title's keywords in the candidate speech block.
  - Flags low confidence matches (or instances where gap detection fails) for manual review.
  - Invokes FFmpeg to cut the matching video segments into individual files named `{slug}.mp4`.

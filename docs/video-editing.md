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


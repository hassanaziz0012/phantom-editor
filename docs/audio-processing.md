# Audio Processing Scripts

This directory contains utility scripts for extracting, cleaning, and normalizing audio streams from video projects.

## Scripts

### [extract_wav_from_mp4.sh](../audio-processing/extract_wav_from_mp4.sh)
Extracts audio from an MP4 video file to a 16-bit, 48 kHz stereo WAV file using FFmpeg.

* **Usage**: `./extract_wav_from_mp4.sh <input.mp4> [--output-file <output.wav>]`
* **Default Output**: `<input_name>.wav`

### [noise_reduction.sh](../audio-processing/noise_reduction.sh)
Applies noise reduction to a WAV file using `deep-filter`.

* **Usage**: `./noise_reduction.sh <input.wav> [--output-file <output.wav> | -o <output.wav>] [-h | --help]`
* **Default Output**: `<input_name>_filtered.wav`

### [normalize_audio.sh](../audio-processing/normalize_audio.sh)
Normalizes audio loudness to standard targets using FFmpeg's `loudnorm` filter (integrated: -16 LUFS, true peak: -1.5 dBTP, LRA: 11).

* **Usage**: `./normalize_audio.sh <input.wav> [--output-file <output.wav> | -o <output.wav>] [-h | --help]`
* **Default Output**: `<input_name>_normalized.wav`

### [process_audio.sh](../audio-processing/process_audio.sh)
Runs the entire audio processing pipeline (extraction, noise filtering, normalization, and track replacement) for video projects.

* **Usage**: 
  - **Single file mode**: `./process_audio.sh <path_to_mp4>`
    - *Requirement*: Input path must reside inside a directory structure containing `yt-projects/<project_name>/`.
    - *Workflow*: Creates a `temp/` folder in the project directory, extracts/processes audio, saves final WAV to `processed-audio.wav`, and saves the combined MP4 to `after-audio-processing.mp4`.
  - **Recursive mode**: `./process_audio.sh -R|--recursive <folder_path>`
    - Processes all video files inside the specified folder.
    - Uses system temp directory (`mktemp -d`) for intermediate files.
    - Saves final video results into a sibling directory named `audio-processed/` relative to the input folder, preserving original subdirectory structures. Does not require the `yt-projects/` folder structure.
* **Pipeline steps**:
  1. Extracts raw WAV audio.
  2. Runs noise reduction via `deep-filter`.
  3. Runs normalization via FFmpeg.
  4. Combines the processed audio track back into the video using `ffmpeg` with copy on the video stream.


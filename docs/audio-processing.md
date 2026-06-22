# Audio Processing Scripts

This directory contains utility scripts for extracting, cleaning, and normalizing audio streams from video projects.

## Scripts

### [extract_wav_from_mp4.sh](../audio-processing/extract_wav_from_mp4.sh)
Extracts audio from an MP4 video file to a 16-bit, 48 kHz stereo WAV file using FFmpeg.
* **Usage**: `./extract_wav_from_mp4.sh <input.mp4> [--output-file <output.wav>]`
* **Default Output**: `<input_name>.wav`

### [noise_reduction.sh](../audio-processing/noise_reduction.sh)
Applies noise reduction to a WAV file using `deep-filter`.
* **Usage**: `./noise_reduction.sh <input.wav> [--output-file <output.wav>]`
* **Default Output**: `<input_name>_filtered.wav`

### [normalize_audio.sh](../audio-processing/normalize_audio.sh)
Normalizes audio loudness to standard targets using FFmpeg's `loudnorm` filter (integrated: -16 LUFS, true peak: -1.5 dBTP, LRA: 11).
* **Usage**: `./normalize_audio.sh <input.wav> [--output-file <output.wav>]`
* **Default Output**: `<input_name>_normalized.wav`

### [process_audio.sh](../audio-processing/process_audio.sh)
Runs the entire audio processing pipeline for an active video project.
* **Pipeline steps**:
  1. Creates a `temp/` folder in the project directory.
  2. Extracts raw WAV audio.
  3. Runs noise reduction via `deep-filter`.
  4. Runs normalization via FFmpeg.
  5. Saves the final processed WAV to `processed-audio.wav`.
  6. Replaces the audio stream in the original MP4, saving the output to `after-audio-processing.mp4`.
* **Usage**: `./process_audio.sh <path_to_mp4>`
* **Requirement**: Input path must reside inside a directory structure containing `yt-projects/<project_name>/`.

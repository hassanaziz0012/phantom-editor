#!/bin/bash

set -e

if [[ -z "$1" ]]; then
    echo "Usage: $0 <path_to_mp4>"
    exit 1
fi

INPUT_MP4="$1"

if [[ ! -f "$INPUT_MP4" ]]; then
    echo "Error: File '$INPUT_MP4' does not exist."
    exit 1
fi

# Extract project name
PROJECT_NAME=$(echo "$INPUT_MP4" | awk -F'yt-projects/' '{print $2}' | cut -d'/' -f1)

if [[ -z "$PROJECT_NAME" ]]; then
    echo "Error: Could not extract project name from path '$INPUT_MP4'."
    echo "The path must contain 'yt-projects/<project_name>/'."
    exit 1
fi

# Get the base directory for the project
PROJECT_DIR="${INPUT_MP4%yt-projects/*}yt-projects/$PROJECT_NAME"

echo "Project Name: $PROJECT_NAME"
echo "Project Dir: $PROJECT_DIR"

TEMP_DIR="$PROJECT_DIR/temp"
RAW_WAV="$TEMP_DIR/raw-audio.wav"
NOISE_REDUCED_WAV="$TEMP_DIR/noise-reduced.wav"
NORMALIZED_WAV="$TEMP_DIR/normalized.wav"
PROCESSED_WAV="$PROJECT_DIR/processed-audio.wav"
FINAL_MP4="$PROJECT_DIR/after-audio-processing.mp4"

# Get the directory of the current script to reliably call the other scripts
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 1. Create a {project}/temp folder.
echo "--- Step 1: Creating temp directory ---"
mkdir -p "$TEMP_DIR"

# 2. Extract its WAV audio
echo "--- Step 2: Extracting RAW audio ---"
"$SCRIPT_DIR/extract_wav_from_mp4.sh" "$INPUT_MP4" --output-file "$RAW_WAV"

# 3. Apply noise reduction
echo "--- Step 3: Applying noise reduction ---"
"$SCRIPT_DIR/noise_reduction.sh" "$RAW_WAV" --output-file "$NOISE_REDUCED_WAV"

# 4. Apply normalization
echo "--- Step 4: Applying normalization ---"
"$SCRIPT_DIR/normalize_audio.sh" "$NOISE_REDUCED_WAV" --output-file "$NORMALIZED_WAV"

# 5. Copy the normalized file to {project}/processed-audio.wav
echo "--- Step 5: Copying final processed audio ---"
cp "$NORMALIZED_WAV" "$PROCESSED_WAV"
# 6. Replace audio in original MP4
echo "--- Step 6: Replacing audio in original MP4 ---"
ffmpeg -y -i "$INPUT_MP4" -i "$PROCESSED_WAV" -c:v copy -map 0:v:0 -map 1:a:0 -c:a aac -b:a 384k "$FINAL_MP4" -hide_banner -loglevel warning

echo "Audio processing pipeline complete!"
echo "Final processed audio is located at: $PROCESSED_WAV"
echo "Final combined video is located at: $FINAL_MP4"

#!/bin/bash

# Default variables
INPUT_FILE=""
OUTPUT_FILE=""

# Parse arguments
while [[ $# -gt 0 ]]; do
  case $1 in
    --output-file)
      OUTPUT_FILE="$2"
      shift 2
      ;;
    *)
      INPUT_FILE="$1"
      shift
      ;;
  esac
done

# Check if input file is provided
if [[ -z "$INPUT_FILE" ]]; then
  echo "Usage: $0 <input.mp4> [--output-file <output.wav>]"
  exit 1
fi

# Set default output name if not provided
if [[ -z "$OUTPUT_FILE" ]]; then
  OUTPUT_FILE="${INPUT_FILE%.*}.wav"
fi

# Run FFmpeg extraction quietly
ffmpeg -loglevel error -i "$INPUT_FILE" \
       -vn \
       -acodec pcm_s16le \
       -ar 48000 \
       -ac 2 \
       -y \
       "$OUTPUT_FILE"

# Check if the command succeeded
if [ $? -eq 0 ]; then
    echo "Extraction complete: $OUTPUT_FILE"
else
    echo "Extraction failed."
fi

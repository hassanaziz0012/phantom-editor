#!/bin/bash

BGM_DIR="/mnt/c/Users/hassa/Videos/Asset Library/BGM"

show_usage() {
    echo "Usage: $0 <path_to_video> <bgm_track>"
    echo ""
    echo "Available BGM tracks:"
    if [ -d "$BGM_DIR" ]; then
        find "$BGM_DIR" -maxdepth 1 -type f -name "*.mp3" -exec basename {} \; | sort
    else
        echo "(BGM directory not found: $BGM_DIR)"
    fi
}

if [ "$#" -eq 0 ]; then
    show_usage
    exit 1
fi

if [ "$#" -lt 2 ]; then
    echo "Error: Missing arguments."
    echo ""
    show_usage
    exit 1
fi

VIDEO_FILE="$1"
BGM_INPUT="$2"

if [ ! -f "$VIDEO_FILE" ]; then
    echo "Error: Video file '$VIDEO_FILE' not found."
    exit 1
fi

if [ -f "$BGM_INPUT" ]; then
    BGM_FILE="$BGM_INPUT"
elif [ -f "$BGM_DIR/$BGM_INPUT" ]; then
    BGM_FILE="$BGM_DIR/$BGM_INPUT"
elif [ -f "$BGM_DIR/$BGM_INPUT.mp3" ]; then
    BGM_FILE="$BGM_DIR/$BGM_INPUT.mp3"
else
    echo "Error: BGM track '$BGM_INPUT' not found."
    exit 1
fi

VIDEO_DIR=$(dirname "$VIDEO_FILE")
VIDEO_BASENAME=$(basename "$VIDEO_FILE")
VIDEO_NAME="${VIDEO_BASENAME%.*}"

OUTPUT_FILE="$VIDEO_DIR/${VIDEO_NAME}-bgm.mp4"

ffmpeg -loglevel error -i "$VIDEO_FILE" -stream_loop -1 -i "$BGM_FILE" -filter_complex "[1:a]volume=0.1[music];[0:a][music]amix=inputs=2:duration=first" -shortest "$OUTPUT_FILE"

if [ $? -eq 0 ]; then
    echo "Successfully created '$OUTPUT_FILE'"
else
    echo "Error occurred during ffmpeg processing."
    exit 1
fi

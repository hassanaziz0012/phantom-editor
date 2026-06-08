#!/bin/bash

BGM_DIR="/mnt/c/Users/hassa/Videos/Asset Library/BGM"

show_usage() {
    echo "Usage: $0 <path_to_video> <bgm_track> [--volume <percentage>]"
    echo ""
    echo "Options for specifying the BGM track:"
    echo "  1. Full path to the BGM file."
    echo "  2. Name of the BGM file in the BGM directory ('$BGM_DIR')."
    echo "  3. Name of the BGM file without the .mp3 extension in the BGM directory."
    echo ""
    echo "Available BGM tracks:"
    if [ -d "$BGM_DIR" ]; then
        find "$BGM_DIR" -maxdepth 1 -type f -name "*.mp3" -exec basename {} \; | sort
    else
        echo "(BGM directory not found: $BGM_DIR)"
    fi
    echo ""
    echo "Optional arguments:"
    echo "  --volume <percentage>  Set the BGM volume (1-100). Default is 10%."
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
VOLUME=10  # Default volume percentage

# Parse optional arguments
shift 2
while [[ "$#" -gt 0 ]]; do
    case "$1" in
        --volume)
            if [[ "$2" =~ ^[1-9][0-9]?$|^100$ ]]; then
                VOLUME="$2"
                shift 2
            else
                echo "Error: Invalid volume percentage '$2'. Must be a number between 1 and 100."
                exit 1
            fi
            ;;
        *)
            echo "Error: Unknown argument '$1'."
            show_usage
            exit 1
            ;;
    esac
done

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

echo "Video file: $VIDEO_FILE"
echo "BGM file: $BGM_FILE"
echo "Selected volume: $VOLUME%"
echo "Starting..."

# Get the total duration of the video in seconds
TOTAL_DURATION=$(ffprobe -v error -select_streams v:0 -show_entries format=duration -of csv=p=0 "$VIDEO_FILE" | awk '{print int($1)}')

if [ -z "$TOTAL_DURATION" ] || [ "$TOTAL_DURATION" -le 0 ]; then
    echo "Error: Unable to determine video duration."
    exit 1
fi

TEMP_PROGRESS_FILE=$(mktemp)

# Start ffmpeg process with progress output redirected to a temporary file
ffmpeg -loglevel error -i "$VIDEO_FILE" -stream_loop -1 -i "$BGM_FILE" \
    -filter_complex "[1:a]volume=${VOLUME}/100[music];[0:a][music]amix=inputs=2:duration=first" \
    -c:v copy -c:a aac -b:a 256k \
    -shortest "$OUTPUT_FILE" -progress "$TEMP_PROGRESS_FILE" &

FFMPEG_PID=$!

# Display progress bar
while kill -0 $FFMPEG_PID 2>/dev/null; do
    if [ -f "$TEMP_PROGRESS_FILE" ]; then
        PROGRESS_MS=$(grep "out_time_ms" "$TEMP_PROGRESS_FILE" | tail -n 1 | cut -d'=' -f2)
        if [ -n "$PROGRESS_MS" ]; then
            PROGRESS_SEC=$((PROGRESS_MS / 1000000))
            PERCENT=$((PROGRESS_SEC * 100 / TOTAL_DURATION))
            BAR=$(printf "%-50s" "$(printf "="%.0s $(seq 1 $((PERCENT / 2))))")
            printf "\rProgress: [%-50s] %d%%" "$BAR" "$PERCENT"
        fi
    fi
    sleep 0.5
done

wait $FFMPEG_PID
EXIT_CODE=$?

# Clean up temporary file
rm -f "$TEMP_PROGRESS_FILE"

if [ $EXIT_CODE -eq 0 ]; then
    echo -e "\nSuccessfully created '$OUTPUT_FILE'"
else
    echo -e "\nError occurred during ffmpeg processing."
    exit 1
fi

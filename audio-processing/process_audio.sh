#!/bin/bash
 
set -e

# Get the directory of the current script to reliably call the other scripts
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

show_usage() {
    echo "Usage: $0 [-R|--recursive] <path>"
    echo "Options:"
    echo "  -R, --recursive   Process all video files inside the specified folder path"
}

RECURSIVE=false
INPUT_PATH=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        -R|--recursive)
            RECURSIVE=true
            shift
            ;;
        -h|--help)
            show_usage
            exit 0
            ;;
        -*)
            echo "Error: Unknown option '$1'"
            show_usage
            exit 1
            ;;
        *)
            if [[ -n "$INPUT_PATH" ]]; then
                echo "Error: Multiple input paths specified."
                show_usage
                exit 1
            fi
            INPUT_PATH="$1"
            shift
            ;;
    esac
done

if [[ -z "$INPUT_PATH" ]]; then
    show_usage
    exit 1
fi

process_video() {
    local INPUT_MP4="$1"
    local OUTPUT_MP4="$2"
    local IS_RECURSIVE="$3"

    if [ "$IS_RECURSIVE" = "true" ]; then
        local TEMP_DIR
        TEMP_DIR=$(mktemp -d)
        
        local RAW_WAV="$TEMP_DIR/raw-audio.wav"
        local NOISE_REDUCED_WAV="$TEMP_DIR/noise-reduced.wav"
        local NORMALIZED_WAV="$TEMP_DIR/normalized.wav"
        local PROCESSED_WAV="$TEMP_DIR/processed-audio.wav"
        
        echo "--- Step 1: Creating temp directory ---"
        # Already created by mktemp -d
        
        echo "--- Step 2: Extracting RAW audio ---"
        "$SCRIPT_DIR/extract_wav_from_mp4.sh" "$INPUT_MP4" --output-file "$RAW_WAV"
        
        echo "--- Step 3: Applying noise reduction ---"
        "$SCRIPT_DIR/noise_reduction.sh" "$RAW_WAV" --output-file "$NOISE_REDUCED_WAV"
        
        echo "--- Step 4: Applying normalization ---"
        "$SCRIPT_DIR/normalize_audio.sh" "$NOISE_REDUCED_WAV" --output-file "$NORMALIZED_WAV"
        
        echo "--- Step 5: Copying final processed audio ---"
        cp "$NORMALIZED_WAV" "$PROCESSED_WAV"
        
        echo "--- Step 6: Replacing audio in original MP4 ---"
        ffmpeg -y -i "$INPUT_MP4" -i "$PROCESSED_WAV" -c:v copy -map 0:v:0 -map 1:a:0 -c:a aac -b:a 384k "$OUTPUT_MP4" -hide_banner -loglevel warning
        
        rm -rf "$TEMP_DIR"
    else
        # Single-file mode
        # Extract project name
        local PROJECT_NAME
        PROJECT_NAME=$(echo "$INPUT_MP4" | awk -F'yt-projects/' '{print $2}' | cut -d'/' -f1)
        
        if [[ -z "$PROJECT_NAME" ]]; then
            echo "Error: Could not extract project name from path '$INPUT_MP4'."
            echo "The path must contain 'yt-projects/<project_name>/'."
            return 1
        fi
        
        local PROJECT_DIR="${INPUT_MP4%yt-projects/*}yt-projects/$PROJECT_NAME"
        
        echo "Project Name: $PROJECT_NAME"
        echo "Project Dir: $PROJECT_DIR"
        
        local TEMP_DIR="$PROJECT_DIR/temp"
        local RAW_WAV="$TEMP_DIR/raw-audio.wav"
        local NOISE_REDUCED_WAV="$TEMP_DIR/noise-reduced.wav"
        local NORMALIZED_WAV="$TEMP_DIR/normalized.wav"
        local PROCESSED_WAV="$PROJECT_DIR/processed-audio.wav"
        local FINAL_MP4="$PROJECT_DIR/after-audio-processing.mp4"
        
        echo "--- Step 1: Creating temp directory ---"
        mkdir -p "$TEMP_DIR"
        
        echo "--- Step 2: Extracting RAW audio ---"
        "$SCRIPT_DIR/extract_wav_from_mp4.sh" "$INPUT_MP4" --output-file "$RAW_WAV"
        
        echo "--- Step 3: Applying noise reduction ---"
        "$SCRIPT_DIR/noise_reduction.sh" "$RAW_WAV" --output-file "$NOISE_REDUCED_WAV"
        
        echo "--- Step 4: Applying normalization ---"
        "$SCRIPT_DIR/normalize_audio.sh" "$NOISE_REDUCED_WAV" --output-file "$NORMALIZED_WAV"
        
        echo "--- Step 5: Copying final processed audio ---"
        cp "$NORMALIZED_WAV" "$PROCESSED_WAV"
        
        echo "--- Step 6: Replacing audio in original MP4 ---"
        ffmpeg -y -i "$INPUT_MP4" -i "$PROCESSED_WAV" -c:v copy -map 0:v:0 -map 1:a:0 -c:a aac -b:a 384k "$FINAL_MP4" -hide_banner -loglevel warning
        
        echo "Audio processing pipeline complete!"
        echo "Final processed audio is located at: $PROCESSED_WAV"
        echo "Final combined video is located at: $FINAL_MP4"
    fi
}

if [ "$RECURSIVE" = true ]; then
    if [[ ! -d "$INPUT_PATH" ]]; then
        echo "Error: Directory '$INPUT_PATH' does not exist."
        exit 1
    fi

    ABS_INPUT_PATH=$(realpath "$INPUT_PATH")
    PARENT_DIR=$(dirname "$ABS_INPUT_PATH")
    OUTPUT_DIR="$PARENT_DIR/audio-processed"

    # Find all video files inside the input folder
    VIDEO_FILES=()
    while IFS= read -r line; do
        if [ -n "$line" ]; then
            VIDEO_FILES+=("$line")
        fi
    done < <(find "$ABS_INPUT_PATH" -type f \( \
        -iname "*.mp4" -o \
        -iname "*.mkv" -o \
        -iname "*.avi" -o \
        -iname "*.mov" -o \
        -iname "*.flv" -o \
        -iname "*.webm" -o \
        -iname "*.wmv" -o \
        -iname "*.m4v" \
    \) ! -iname "*after-audio-processing*" | sort)

    if [ ${#VIDEO_FILES[@]} -eq 0 ]; then
        echo "No video files found in directory: $INPUT_PATH"
        exit 0
    fi

    echo "Found ${#VIDEO_FILES[@]} video file(s) to process recursively."
    SUCCESS_COUNT=0
    FAILURE_COUNT=0

    for idx in "${!VIDEO_FILES[@]}"; do
        VIDEO_FILE="${VIDEO_FILES[$idx]}"
        NUM=$((idx + 1))
        echo ""
        echo "[$NUM/${#VIDEO_FILES[@]}] Processing: $VIDEO_FILE"
        
        ABS_VIDEO_FILE=$(realpath "$VIDEO_FILE")
        REL_PATH=$(realpath --relative-to="$ABS_INPUT_PATH" "$ABS_VIDEO_FILE")
        OUTPUT_FILE="$OUTPUT_DIR/$REL_PATH"
        mkdir -p "$(dirname "$OUTPUT_FILE")"

        # Run in a subshell to ensure set -e handles errors within process_video properly
        if (set -e; process_video "$VIDEO_FILE" "$OUTPUT_FILE" "true"); then
            SUCCESS_COUNT=$((SUCCESS_COUNT + 1))
            echo "Successfully processed: $VIDEO_FILE"
            echo "Output saved to: $OUTPUT_FILE"
        else
            echo "Failed to process: $VIDEO_FILE"
            FAILURE_COUNT=$((FAILURE_COUNT + 1))
        fi
    done

    echo ""
    echo "Done! Success: $SUCCESS_COUNT, Failed: $FAILURE_COUNT"
    if [ "$FAILURE_COUNT" -gt 0 ]; then
        exit 1
    fi
else
    if [[ ! -f "$INPUT_PATH" ]]; then
        echo "Error: File '$INPUT_PATH' does not exist."
        exit 1
    fi
    process_video "$INPUT_PATH" "" "false"
fi

#!/bin/bash
 
BGM_DIR="${BGM_DIR:-$HOME/Videos/Asset Library/BGM}"
 
show_usage() {
    echo "Usage: $0 <path_to_video_or_folder> <bgm_track> [--volume <percentage>] [-R|--recursive] [--pipeline-mode] [--processed-audio <path>]"
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
    echo "  --volume <percentage>      Set the BGM volume (1-100). Default is 10%."
    echo "  -R, --recursive            Recursively process videos if input path is a folder."
    echo "  --pipeline-mode            Pipeline mode: single remux pass with pre-processed audio."
    echo "  --processed-audio <path>   Path to pre-processed audio file (required with --pipeline-mode)."
}
 
if [ "$#" -eq 0 ]; then
    show_usage
    exit 1
fi
 
VOLUME=10  # Default volume percentage
RECURSIVE=false
PIPELINE_MODE=false
PROCESSED_AUDIO=""
POSITIONAL_ARGS=()
 
# Parse arguments
while [[ "$#" -gt 0 ]]; do
    case "$1" in
        -R|--recursive)
            RECURSIVE=true
            shift
            ;;
        --volume)
            if [[ "$2" =~ ^[1-9][0-9]?$|^100$ ]]; then
                VOLUME="$2"
                shift 2
            else
                echo "Error: Invalid volume percentage '$2'. Must be a number between 1 and 100."
                exit 1
            fi
            ;;
        --pipeline-mode)
            PIPELINE_MODE=true
            shift
            ;;
        --processed-audio)
            if [[ -n "$2" ]]; then
                PROCESSED_AUDIO="$2"
                shift 2
            else
                echo "Error: --processed-audio requires a path argument."
                exit 1
            fi
            ;;
        -*)
            echo "Error: Unknown argument '$1'."
            show_usage
            exit 1
            ;;
        *)
            POSITIONAL_ARGS+=("$1")
            shift
            ;;
    esac
done
 
if [ "${#POSITIONAL_ARGS[@]}" -lt 2 ]; then
    echo "Error: Missing arguments."
    echo ""
    show_usage
    exit 1
fi
 
if [ "${#POSITIONAL_ARGS[@]}" -gt 2 ]; then
    echo "Error: Too many positional arguments specified."
    echo ""
    show_usage
    exit 1
fi
 
INPUT_PATH="${POSITIONAL_ARGS[0]}"
BGM_INPUT="${POSITIONAL_ARGS[1]}"
 
# Validate BGM track
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
 
# Validate pipeline mode requirements
if [ "$PIPELINE_MODE" = true ] && [ -z "$PROCESSED_AUDIO" ]; then
    echo "Error: --pipeline-mode requires --processed-audio <path> argument."
    exit 1
fi
 
if [ "$PIPELINE_MODE" = true ] && [ ! -f "$PROCESSED_AUDIO" ]; then
    echo "Error: Processed audio file '$PROCESSED_AUDIO' not found."
    exit 1
fi

process_video() {
    local VIDEO_FILE="$1"
    local OUTPUT_FILE="$2"
    local IS_PIPELINE_MODE="${3:-false}"
    local PROCESSED_AUDIO_FILE="${4:-}"
    
    if [ -z "$OUTPUT_FILE" ] && [ "$IS_PIPELINE_MODE" = "false" ]; then
        local VIDEO_DIR=$(dirname "$VIDEO_FILE")
        local VIDEO_BASENAME=$(basename "$VIDEO_FILE")
        local VIDEO_NAME="${VIDEO_BASENAME%.*}"
        OUTPUT_FILE="$VIDEO_DIR/${VIDEO_NAME}-bgm.mp4"
    fi

    echo "Video file: $VIDEO_FILE"
    echo "BGM file: $BGM_FILE"
    echo "Selected volume: $VOLUME%"
    if [ "$IS_PIPELINE_MODE" = "true" ]; then
        echo "Processed audio: $PROCESSED_AUDIO_FILE"
        echo "Pipeline mode: single remux pass"
    fi
    echo "Starting..."

    # Get the total duration of the video in seconds
    local TOTAL_DURATION
    TOTAL_DURATION=$(ffprobe -v error -select_streams v:0 -show_entries format=duration -of csv=p=0 "$VIDEO_FILE" | awk '{print int($1)}')

    if [ -z "$TOTAL_DURATION" ] || [ "$TOTAL_DURATION" -le 0 ]; then
        echo "Error: Unable to determine video duration for '$VIDEO_FILE'."
        return 1
    fi

    local TEMP_PROGRESS_FILE
    TEMP_PROGRESS_FILE=$(mktemp)

    if [ "$IS_PIPELINE_MODE" = "true" ]; then
        # Pipeline mode: use pre-processed audio + BGM, copy video stream
        # Input 0: video file (video only, ignore its audio)
        # Input 1: processed audio (main audio track)
        # Input 2: BGM file (looped)
        ffmpeg -loglevel error -threads 0 -i "$VIDEO_FILE" -i "$PROCESSED_AUDIO_FILE" -stream_loop -1 -i "$BGM_FILE" \
            -filter_complex "[2:a]volume=${VOLUME}/100[music];[1:a][music]amix=inputs=2:duration=first:dropout_transition=0[out_a]" \
            -map 0:v:0 -map "[out_a]" \
            -c:v copy -c:a aac -b:a 256k -ac 2 \
            -shortest "$OUTPUT_FILE" -progress "$TEMP_PROGRESS_FILE" &
    else
        # Standalone mode: mix video's original audio with BGM
        ffmpeg -loglevel error -threads 0 -i "$VIDEO_FILE" -stream_loop -1 -i "$BGM_FILE" \
            -filter_complex "[1:a]volume=${VOLUME}/100[music];[0:a][music]amix=inputs=2:duration=first" \
            -c:v copy -c:a aac -b:a 256k \
            -shortest "$OUTPUT_FILE" -progress "$TEMP_PROGRESS_FILE" &
    fi

    local FFMPEG_PID=$!

    # Display progress bar
    while kill -0 $FFMPEG_PID 2>/dev/null; do
        if [ -f "$TEMP_PROGRESS_FILE" ]; then
            local PROGRESS_MS
            PROGRESS_MS=$(grep "out_time_ms" "$TEMP_PROGRESS_FILE" | tail -n 1 | cut -d'=' -f2)
            if [ -n "$PROGRESS_MS" ]; then
                local PROGRESS_SEC=$((PROGRESS_MS / 1000000))
                local PERCENT=$((PROGRESS_SEC * 100 / TOTAL_DURATION))
                if [ "$PERCENT" -gt 100 ]; then
                    PERCENT=100
                fi
                local BAR_COUNT=$((PERCENT / 2))
                local BAR=""
                for ((i=0; i<BAR_COUNT; i++)); do
                    BAR="${BAR}="
                done
                BAR=$(printf "%-50s" "$BAR")
                printf "\rProgress: [%-50s] %d%%" "$BAR" "$PERCENT"
            fi
        fi
        sleep 0.5
    done

    wait $FFMPEG_PID
    local EXIT_CODE=$?

    # Clean up temporary file
    rm -f "$TEMP_PROGRESS_FILE"

    if [ $EXIT_CODE -eq 0 ]; then
        echo -e "\nSuccessfully created '$OUTPUT_FILE'"
        return 0
    else
        echo -e "\nError occurred during ffmpeg processing."
        return 1
    fi
}

if [ -d "$INPUT_PATH" ]; then
    # Directory processing
    if [ "$RECURSIVE" = true ]; then
        # Recursive find
        MAPFILE_CMD=(find "$INPUT_PATH" -type f \( -iname "*.mp4" -o -iname "*.mkv" -o -iname "*.avi" -o -iname "*.mov" -o -iname "*.flv" -o -iname "*.webm" -o -iname "*.wmv" -o -iname "*.m4v" \) ! -iname "*-bgm.*")
    else
        # Top-level find only
        MAPFILE_CMD=(find "$INPUT_PATH" -maxdepth 1 -type f \( -iname "*.mp4" -o -iname "*.mkv" -o -iname "*.avi" -o -iname "*.mov" -o -iname "*.flv" -o -iname "*.webm" -o -iname "*.wmv" -o -iname "*.m4v" \) ! -iname "*-bgm.*")
    fi

    # Read the files into an array
    VIDEO_FILES=()
    while IFS= read -r line; do
        if [ -n "$line" ]; then
            VIDEO_FILES+=("$line")
        fi
    done < <("${MAPFILE_CMD[@]}" | sort)

    if [ ${#VIDEO_FILES[@]} -eq 0 ]; then
        echo "No video files found in directory: $INPUT_PATH"
        exit 0
    fi

    ABS_INPUT_PATH=$(realpath "$INPUT_PATH")
    PARENT_DIR=$(dirname "$ABS_INPUT_PATH")
    OUTPUT_DIR="$PARENT_DIR/added_bgm"

    echo "Found ${#VIDEO_FILES[@]} video file(s) to process."
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

        if process_video "$VIDEO_FILE" "$OUTPUT_FILE" "false" ""; then
            SUCCESS_COUNT=$((SUCCESS_COUNT + 1))
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
    # Single file processing
    if [ ! -f "$INPUT_PATH" ]; then
        echo "Error: Video file or directory '$INPUT_PATH' not found."
        exit 1
    fi
    process_video "$INPUT_PATH" "" "false" ""
    exit $?
fi

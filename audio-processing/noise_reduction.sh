#!/bin/bash

set -e

print_usage() {
    echo "Usage: $0 <input_file.wav> [--output-file <output_file.wav>]"
    echo ""
    echo "Options:"
    echo "  --output-file    Specify the output filename. Default: <input_filename>_filtered.wav"
    echo "  -h, --help       Print this help message"
}

INPUT_FILE=""
OUTPUT_FILE=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --output-file|-o)
            OUTPUT_FILE="$2"
            shift 2
            ;;
        -h|--help)
            print_usage
            exit 0
            ;;
        -*)
            echo "Unknown option: $1"
            print_usage
            exit 1
            ;;
        *)
            if [[ -z "$INPUT_FILE" ]]; then
                INPUT_FILE="$1"
                shift
            else
                echo "Error: Multiple input files not supported."
                print_usage
                exit 1
            fi
            ;;
    esac
done

if [[ -z "$INPUT_FILE" ]]; then
    echo "Error: No input file specified."
    print_usage
    exit 1
fi

if [[ ! -f "$INPUT_FILE" ]]; then
    echo "Error: Input file '$INPUT_FILE' does not exist."
    exit 1
fi

# Set default output filename if not provided
if [[ -z "$OUTPUT_FILE" ]]; then
    BASENAME=$(basename "$INPUT_FILE")
    NAME="${BASENAME%.*}"
    EXT="${BASENAME##*.}"
    OUTPUT_FILE="${NAME}_filtered.${EXT}"
fi

# Create a temporary directory for deep-filter processing
TMP_DIR=$(mktemp -d)

# Ensure the temp directory is cleaned up on exit
trap 'rm -rf "$TMP_DIR"' EXIT

# Detect available CPU cores
CORES=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 1)

# Get audio duration in seconds
DURATION=$(ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "$INPUT_FILE" 2>/dev/null | awk '{print int($1)}')

echo "Processing '$INPUT_FILE' with deep-filter (using $CORES CPU core(s))..."

GENERATED_FILE="$TMP_DIR/$(basename "$INPUT_FILE")"

if [[ -n "$DURATION" && "$DURATION" -ge 15 && "$CORES" -gt 1 ]]; then
    # Multi-threaded parallel chunk processing across CPU cores
    CHUNK_DIR="$TMP_DIR/chunks"
    OUT_CHUNK_DIR="$TMP_DIR/out_chunks"
    mkdir -p "$CHUNK_DIR" "$OUT_CHUNK_DIR"

    # Calculate optimal segment length per core (minimum 10s per chunk)
    CHUNK_LEN=$(( (DURATION + CORES - 1) / CORES ))
    if [[ $CHUNK_LEN -lt 10 ]]; then
        CHUNK_LEN=10
    fi

    # Split audio into chunks
    ffmpeg -hide_banner -loglevel error -i "$INPUT_FILE" -f segment -segment_time "$CHUNK_LEN" -c copy "$CHUNK_DIR/chunk_%04d.wav"

    # Run deep-filter in parallel across all CPU cores
    find "$CHUNK_DIR" -maxdepth 1 -name "chunk_*.wav" | sort | xargs -P "$CORES" -I {} deep-filter -o "$OUT_CHUNK_DIR" {}

    # Concatenate processed audio chunks
    CONCAT_LIST="$TMP_DIR/concat.txt"
    find "$OUT_CHUNK_DIR" -maxdepth 1 -name "chunk_*.wav" | sort | sed "s/^/file '/; s/$/'/" > "$CONCAT_LIST"
    ffmpeg -hide_banner -loglevel error -f concat -safe 0 -i "$CONCAT_LIST" -c copy "$GENERATED_FILE"
else
    # Fallback to standard single-threaded execution for short audio files or single-core systems
    deep-filter -o "$TMP_DIR" "$INPUT_FILE"
fi

if [[ -f "$GENERATED_FILE" ]]; then
    mv "$GENERATED_FILE" "$OUTPUT_FILE"
    echo "Success! Saved filtered audio to '$OUTPUT_FILE'"
else
    # Fallback in case deep-filter changes the filename format
    FOUND_FILE=$(find "$TMP_DIR" -type f -name "*.wav" | head -n 1)
    if [[ -n "$FOUND_FILE" ]]; then
        mv "$FOUND_FILE" "$OUTPUT_FILE"
        echo "Success! Saved filtered audio to '$OUTPUT_FILE'"
    else
        echo "Error: deep-filter did not generate an output file."
        exit 1
    fi
fi

#!/bin/bash

set -e

print_usage() {
    echo "Usage: $0 <input_file.wav> [--output-file <output_file.wav>]"
    echo ""
    echo "Options:"
    echo "  --output-file, -o    Specify the output filename. Default: <input_filename>_normalized.wav"
    echo "  -h, --help           Print this help message"
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
    if [[ -z "$EXT" || "$EXT" == "$NAME" ]]; then
        # No extension
        OUTPUT_FILE="${NAME}_normalized.wav"
    else
        OUTPUT_FILE="${NAME}_normalized.${EXT}"
    fi
fi

echo "Normalizing '$INPUT_FILE' to '$OUTPUT_FILE'..."

# Run ffmpeg normalization
if ! ffmpeg -y -i "$INPUT_FILE" -af "loudnorm=I=-16:TP=-1.5:LRA=11" "$OUTPUT_FILE" -hide_banner -loglevel warning; then
    echo "Error: ffmpeg failed."
    exit 1
fi

echo "Success! Saved normalized audio to '$OUTPUT_FILE'"

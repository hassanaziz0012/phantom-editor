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

# Create a temporary directory for deep-filter processing.
# Prefer /dev/shm (RAM disk) so DeepFilterNet chunk splitting and the
# intermediate WAV files never touch the physical disk. Fall back to a
# standard mktemp directory when /dev/shm is unavailable, unwritable,
# or too small for the estimated working set.
TMP_DIR=""
if [[ -d "/dev/shm" && -w "/dev/shm" ]]; then
    # Peak working set ≈ 5x the input WAV (input copy + chunked copy +
    # filtered chunks + concatenated output) plus a small safety margin.
    INPUT_SIZE_MB=$(du -m "$INPUT_FILE" 2>/dev/null | awk '{printf "%d", $1 + 1}')
    NEEDED_MB=$(( ${INPUT_SIZE_MB:-0} * 5 + 32 ))
    AVAIL_MB=$(df -Pm /dev/shm 2>/dev/null | awk 'NR==2 {print $4}')
    if [[ -n "$AVAIL_MB" && "$AVAIL_MB" -ge "$NEEDED_MB" ]]; then
        TMP_DIR=$(mktemp -d -p /dev/shm deepfilter_XXXXXX)
        echo "Using RAM disk (/dev/shm) for intermediate files: $TMP_DIR"
    else
        echo "Warning: /dev/shm unavailable or low on space (need ~${NEEDED_MB}MiB), using standard temp dir." >&2
    fi
fi
if [[ -z "$TMP_DIR" ]]; then
    TMP_DIR=$(mktemp -d)
fi

# Ensure the temp directory is cleaned up on exit
trap 'rm -rf "$TMP_DIR"' EXIT

# Detect available CPU cores
CORES=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 1)

# Sample rate and exact length in samples: chunk boundaries are computed in
# samples so the filtered chunks add back up to precisely the input length.
IFS=, read -r SAMPLE_RATE TOTAL_SAMPLES < <(ffprobe -v error -select_streams a:0 \
    -show_entries stream=sample_rate,duration_ts -of csv=p=0 "$INPUT_FILE")
if [[ -z "$SAMPLE_RATE" || -z "$TOTAL_SAMPLES" || "$TOTAL_SAMPLES" -le 0 ]]; then
    echo "Error: could not read sample rate / length of '$INPUT_FILE'."
    exit 1
fi
DURATION=$(( TOTAL_SAMPLES / SAMPLE_RATE ))

echo "Processing '$INPUT_FILE' with deep-filter (using $CORES CPU core(s))..."

GENERATED_FILE="$TMP_DIR/$(basename "$INPUT_FILE")"
CHUNK_DIR="$TMP_DIR/chunks"
OUT_CHUNK_DIR="$TMP_DIR/out_chunks"
FINAL_CHUNK_DIR="$TMP_DIR/final_chunks"
mkdir -p "$CHUNK_DIR" "$OUT_CHUNK_DIR" "$FINAL_CHUNK_DIR"

if [[ "$DURATION" -ge 15 && "$CORES" -gt 1 ]]; then
    # Fine-grained chunking instead of a coarse per-core split. A fixed short chunk
    # keeps every unit of work small and roughly uniform, so no single chunk with
    # heavy speech/noise can become a straggler that leaves the other cores idle.
    # A 7-minute file yields ~21 chunks rather than 4 x ~105s chunks.
    CHUNK_LEN=20
    JOBS=$CORES
    echo "Splitting audio into ${CHUNK_LEN}s chunks for dynamic scheduling across $CORES core(s)..."
else
    # Short audio or a single core: one chunk covering the whole file.
    CHUNK_LEN=$(( DURATION + 1 ))
    JOBS=1
fi

# deep-filter delays its output by the STFT + model lookahead (~30ms). With
# -D it compensates by dropping that much from the start, so its output is
# ~30ms SHORTER than its input. Each chunk is therefore cut with overlap and
# trimmed back afterwards:
#   - TAIL extra samples after the chunk (real audio, or zero padding at the
#     end of the file) so -D still has the chunk's last samples to emit;
#   - HEAD extra samples before it, so the model's recurrent state has warmed
#     up by the time the kept part starts.
# The kept parts then tile the input exactly, sample for sample.
CHUNK_SAMPLES=$(( CHUNK_LEN * SAMPLE_RATE ))
HEAD_SAMPLES=$(( SAMPLE_RATE / 2 ))
TAIL_SAMPLES=$(( SAMPLE_RATE / 10 ))

# One job per chunk: "<index> <cut start> <cut end> <samples to skip> <samples to keep>"
JOB_LIST="$TMP_DIR/jobs.txt"
: > "$JOB_LIST"
INDEX=0
for (( START = 0; START < TOTAL_SAMPLES; START += CHUNK_SAMPLES )); do
    END=$(( START + CHUNK_SAMPLES < TOTAL_SAMPLES ? START + CHUNK_SAMPLES : TOTAL_SAMPLES ))
    CUT_START=$(( START > HEAD_SAMPLES ? START - HEAD_SAMPLES : 0 ))
    CUT_END=$(( END + TAIL_SAMPLES < TOTAL_SAMPLES ? END + TAIL_SAMPLES : TOTAL_SAMPLES ))
    printf '%04d %d %d %d %d\n' "$INDEX" "$CUT_START" "$CUT_END" \
        $(( START - CUT_START )) $(( END - START )) >> "$JOB_LIST"
    INDEX=$(( INDEX + 1 ))
done

filter_chunk() {
    local INDEX=$1 CUT_START=$2 CUT_END=$3 SKIP=$4 KEEP=$5
    local NAME="chunk_$INDEX.wav"
    ffmpeg -hide_banner -loglevel error -i "$INPUT_FILE" \
        -af "atrim=start_sample=$CUT_START:end_sample=$CUT_END,apad=pad_len=$TAIL_SAMPLES" \
        -c:a pcm_s16le "$CHUNK_DIR/$NAME"
    deep-filter -D -o "$OUT_CHUNK_DIR" "$CHUNK_DIR/$NAME"
    ffmpeg -hide_banner -loglevel error -i "$OUT_CHUNK_DIR/$NAME" \
        -af "atrim=start_sample=$SKIP:end_sample=$(( SKIP + KEEP ))" \
        -c:a pcm_s16le "$FINAL_CHUNK_DIR/$NAME"
}
export -f filter_chunk
export INPUT_FILE CHUNK_DIR OUT_CHUNK_DIR FINAL_CHUNK_DIR TAIL_SAMPLES

# Dynamic work queue: xargs keeps exactly $JOBS chunk jobs in flight and
# dispatches the next chunk the instant any core frees up, so total wall time
# tracks the total amount of work instead of the slowest single chunk.
xargs -P "$JOBS" -L 1 bash -c 'set -e; filter_chunk "$@"' _ < "$JOB_LIST"

# Concatenate the trimmed chunks in playback order (zero-padded names sort correctly)
CONCAT_LIST="$TMP_DIR/concat.txt"
find "$FINAL_CHUNK_DIR" -maxdepth 1 -name "chunk_*.wav" | sort | sed "s/^/file '/; s/$/'/" > "$CONCAT_LIST"
ffmpeg -hide_banner -loglevel error -f concat -safe 0 -i "$CONCAT_LIST" -c copy "$GENERATED_FILE"

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

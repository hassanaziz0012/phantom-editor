#!/bin/bash

# Exit on error
set -e

# Get the root directory of this codebase (where this script is located)
BASE_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"

# Check if cloc is installed
if ! command -v cloc &> /dev/null; then
    echo "Error: cloc is not installed." >&2
    echo "Please install cloc (e.g., 'sudo apt install cloc' on Debian/Ubuntu)." >&2
    exit 1
fi

# Run cloc from the codebase root directory to get relative paths in the output
cd "$BASE_DIR"

# Run cloc to count LOC in Python and Bash files, ignoring the .venv directory.
# By default, --by-file lists files sorted by code LOC in descending order.
cloc --by-file \
     --include-lang="Python,Bourne Shell,Bourne Again Shell" \
     --exclude-dir=".venv" \
     .

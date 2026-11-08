#!/bin/bash

# Exit immediately if a command exits with a non-zero status
set -e

# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
PHANTOM_SRC="$SCRIPT_DIR/phantom"
PHANTOM_DEST="$HOME/.local/bin/phantom"

# Ensure the phantom source file exists
if [ ! -f "$PHANTOM_SRC" ]; then
    echo "Error: Source file '$PHANTOM_SRC' not found."
    exit 1
fi

# Ensure target directory exists
mkdir -p "$HOME/.local/bin"

# Make sure the source script is executable
chmod +x "$PHANTOM_SRC"

# A symlink, not a copy: phantom finds the repo by following it, and edits to the
# script take effect without reinstalling.
echo "Linking $PHANTOM_DEST to $PHANTOM_SRC..."
ln -sfn "$PHANTOM_SRC" "$PHANTOM_DEST"

echo "Successfully installed phantom CLI at $PHANTOM_DEST!"

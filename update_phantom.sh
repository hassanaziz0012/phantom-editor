#!/bin/bash

# Exit immediately if a command exits with a non-zero status
set -e

# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
PHANTOM_SRC="$SCRIPT_DIR/phantom"
PHANTOM_DEST="/usr/local/bin/phantom"

# Ensure the phantom source file exists
if [ ! -f "$PHANTOM_SRC" ]; then
    echo "Error: Source file '$PHANTOM_SRC' not found."
    exit 1
fi

# Make sure the source script is executable
chmod +x "$PHANTOM_SRC"

echo "Copying phantom CLI to $PHANTOM_DEST..."
# Use sudo to copy to /usr/local/bin
sudo cp "$PHANTOM_SRC" "$PHANTOM_DEST"

# Ensure the copied file is executable
sudo chmod +x "$PHANTOM_DEST"

echo "Successfully updated phantom CLI at $PHANTOM_DEST!"

#!/bin/bash

# Check if an argument is provided
if [ $# -eq 0 ]; then
    echo "Usage: tweet <your_tweet_content>"
    exit 1
fi

# Run the python script using uv from the correct project directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
BASE_DIR="$( cd "$SCRIPT_DIR/.." &> /dev/null && pwd )"
uv run --project "$BASE_DIR" python "$BASE_DIR/twitter/post_tweet.py" "$@"

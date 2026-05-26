#!/bin/bash

# Check if an argument is provided
if [ $# -eq 0 ]; then
    echo "Usage: tweet <your_tweet_content>"
    exit 1
fi

# Run the python script using uv from the correct project directory
# "$@" passes all arguments exactly as provided, preserving quoting and flags
uv run --project /home/hassan/programming/phantom-editor python /home/hassan/programming/phantom-editor/twitter/post_tweet.py "$@"

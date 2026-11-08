# Twitter (X) Integration

This directory contains automation scripts to sign in and post tweets using Playwright.

## Scripts

### [login.py](../twitter/login.py)
Launches a non-headless browser session to allow manual login to X.com.

* **Usage**: `uv run python -m shared.launch twitter.login`
* **Output**: Saves authentication state to `auth.json` (used for subsequent automated actions).

### [post_tweet.py](../twitter/post_tweet.py)
Automates tweeting by launching Playwright with saved credentials from `auth.json`. Supports attaching an image from the clipboard and cross-posting to Substack Notes.

* **Usage**: `phantom publish x [--image] [--substack] "tweet content"`

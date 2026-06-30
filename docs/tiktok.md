# TikTok Integration

This directory contains scripts to automate uploading short-form video content to TikTok.

## Configuration
Authentication with TikTok is performed through multiple fallback methods in the following order of priority:
1. **Cookies Files**:
   - `tiktok_cookies.txt` or `cookies.txt` at the repository root. If either file exists, the browser will use these cookies to authenticate directly. You can export cookies from your browser session using standard Netscape cookies format extensions.
2. **Session ID**:
   - `TIKTOK_SESSIONID` or `TIKTOK_SESSION_ID` environment variables.
3. **Environment Credentials**:
   - `TIKTOK_USERNAME` and `TIKTOK_PASSWORD` environment variables.

> [!NOTE]
> If using environment credentials directly, the script automatically defaults to headed mode (visible browser window) to allow you to solve any CAPTCHAs, verification puzzles, or 2FA prompts that appear during the login flow.

## Scripts

### [upload_tiktok.py](../tiktok/upload_tiktok.py)
Launches Playwright underneath using the `tiktok-uploader` library to upload a video to TikTok.
* **Usage**: `python tiktok/upload_tiktok.py <path_to_video.mp4> [--headed]` or via `phantom shorts upload --platform tiktok <video_path>`
* **Metadata Lookup**:
  - Automatically queries the unified database `shorts/shorts.json` to find matching metadata for the video (matching by path, filename, or stem).
  - Forms the caption using the title, description (formatted with the description template defined in `config.py`), and tags formatted as hashtags.
* **Key Features**:
  - **Browser Mode**: Runs browser in headless mode by default (except when using username/password without cookies). Pass `--headed` to run in a visible browser.
  - **Skip Check**: Checks if the TikTok video is already marked as posted in `shorts/shorts.json` and skips to prevent duplicates.
  - **Status Update**: Updates the `posted.tiktok` status to `True` in `shorts/shorts.json` upon successful upload.

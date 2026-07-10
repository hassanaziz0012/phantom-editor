# Instagram Integration

This directory contains scripts to automate uploading short-form video content to Instagram as Reels.

## Configuration
Requires the following environment variables to be set in your `.env` file at the repository root:

- `INSTAGRAM_USERNAME`: Your Instagram account username.
- `INSTAGRAM_PASSWORD`: Your Instagram account password.

Other related session files:

- `instagram_session.json`: Created automatically at the repository root to cache login sessions and bypass challenge requirements.

## Scripts

### [upload_reel.py](../instagram/upload_reel.py)
Authenticates with Instagram and uploads a video as a Reel using `instagrapi`.

* **Usage**: `python instagram/upload_reel.py <path_to_video.mp4>` or via `phantom shorts upload --platform instagram <video_path>`
* **Metadata Lookup**:
  - Automatically queries the unified database `shorts/shorts.json` to find matching metadata for the video (matching by path).
  - Uses the description to form the caption (formatted with the global description template defined in `config.py`).
  - Uses the custom thumbnail specified in the metadata if available.
* **Key Features**:
  - **Session Caching**: Stores the login settings in `instagram_session.json` to prevent repeated logging in and triggering security checks.
  - **Auto-generated Thumbnails**: If no thumbnail is specified in the metadata, the script automatically extracts the first frame at `00:00:01` using `ffmpeg` and uses it as the cover before cleaning it up.
  - **Skip Check**: Checks if the Reel is already marked as posted to Instagram in `shorts/shorts.json` and skips to prevent duplicates.
  - **Status Update**: Updates the `posted.instagram` status to `True` in `shorts/shorts.json` upon successful upload.

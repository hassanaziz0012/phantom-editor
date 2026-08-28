# Substack Integration

This directory contains automation scripts to publish notes with text, images, and video attachments to Substack (`substack.com`) using Playwright over Chrome DevTools Protocol (CDP).

## Architecture

Follows the persistent Chrome CDP standard:
- Attaches to Google Chrome via CDP on port `9222`.
- Shares the central browser profile (`~/Desktop/browser-profiles/cdp`), keeping sessions and cookies intact.
- Uses role and accessibility-based selectors for reliability.
- Injects stealth scripts to mask automation flags and grants clipboard permissions to retrieve direct post URLs.

## Scripts

### [post_note.py](../substack/post_note.py)

Automates publishing short-form Substack Notes with optional image or video attachments.

* **CLI Command**:
  ```bash
  # Text-only note:
  phantom substack note "Just launched our new automation pipeline!"

  # Note with an image:
  phantom substack note "Check out our architecture diagram:" -i ./diagram.png

  # Note with multiple images:
  phantom substack note "Visual updates:" -i ./img1.png ./img2.png

  # Note with video:
  phantom substack note "Demo video below:" -v ./demo.mp4

  # Headless mode:
  phantom substack note "Automated announcement" --headless
  ```

* **Direct Python Usage**:
  ```bash
  uv run python substack/post_note.py "Note content" [-i <images>...] [-v <video>] [--headless]
  ```

* **Options**:
  - `text` *(positional)*: Text content of the note.
  - `-t, --text`: Alternative flag for note text content.
  - `-i, --image, --images`: One or more paths to image files (`.png`, `.jpg`, `.jpeg`, `.webp`).
  - `-v, --video`: Path to a video file (`.mp4`, `.mov`, etc.).
  - `--headless`: Launch Chrome in headless mode if not already running.

* **Features & Workflow**:
  - Automatically verifies Chrome CDP availability on port `9222` and starts the instance if needed.
  - Navigates to `https://substack.com/home` and activates the *"What's on your mind?"* composer modal.
  - Attaches images or video via hidden file input elements.
  - Types the note text into the rich-text composer.
  - Monitors the *"Post"* button until uploads finish processing and clicks to publish.
  - Automatically clicks the *Share* menu on the newly posted note, copies the share link to clipboard, and outputs the direct post URL.

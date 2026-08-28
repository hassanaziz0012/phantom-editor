# Threads Integration

This directory contains automation scripts to publish single posts and multi-part threads with optional image attachments to Threads (`threads.com` / `threads.net`) using Playwright over Chrome DevTools Protocol (CDP).

## Architecture

Follows the persistent Chrome CDP standard:
- Attaches to Google Chrome via CDP on port `9222`.
- Shares the central browser profile (`~/Desktop/browser-profiles/cdp`), keeping sessions and cookies intact.
- Uses role and accessibility-based selectors for reliability.
- Injects stealth scripts to mask automation flags.

## Scripts

### [post_thread.py](../threads/post_thread.py)

Automates publishing single posts or multi-post threads with optional image attachments.

* **CLI Command**:
  ```bash
  # Single post from direct text:
  phantom threads post "🎬 New video just dropped! https://youtu.be/abc123xyz"

  # Single post from a text file:
  phantom threads post --posts post1.txt

  # Multi-post thread from multiple text files:
  phantom threads post --posts post1.txt post2.txt post3.txt

  # Thread with image attachment(s):
  phantom threads post --posts post1.txt post2.txt --images image1.png image2.png

  # Headless mode:
  phantom threads post "Quick update!" --headless
  ```

* **Direct Python Usage**:
  ```bash
  uv run python threads/post_thread.py [<text>] [--posts <file...>] [--images <images...>] [--headless]
  ```

* **Options**:
  - `text`: Optional text content for a single post (or passed via `-t, --text`).
  - `-p, --posts`: List of file paths to text documents (each $\le 500$ characters).
  - `-i, --image, --images`: Optional path(s) to local image file(s) to attach.
  - `--headless`: Launch Chrome in headless mode if not already running.

* **Features & Workflow**:
  - Automatically verifies Chrome CDP availability on port `9222` and starts the instance if needed.
  - Pre-validates character count ($\le 500$ characters per post) for every input file before opening the browser.
  - Navigates to `https://www.threads.com` and triggers the composer modal.
  - Sequentially inputs each post in order, automatically clicking *"Add to thread"* for multi-part threads.
  - Attaches image files via file inputs.
  - Waits for media uploads and confirms the *"Post"* button is enabled before submission.
  - Captures the direct post URL from the *"View"* link in the success toast notification.

# DEV Community Integration

This directory contains automation scripts to publish posts to DEV Community (`dev.to`) from Markdown files using Playwright over Chrome DevTools Protocol (CDP).

## Architecture

Follows the persistent Chrome CDP standard:
- Attaches to Google Chrome via CDP on port `9222`, using the shared helpers in `browser/cdp.py`.
- Uses the `~/Desktop/browser-profiles/p9222` browser profile, which must already be logged in to dev.to.
- Drives the regular `https://dev.to/new` editor, so no API key is needed.

## Scripts

### [post.py](../devto/post.py)

Creates a new dev.to post from a Markdown file, with title, up to 4 tags, and an optional cover image, canonical URL, series and scheduled publish time. Publishes immediately unless `--draft` is given, and prints the post's URL.

* **CLI Command**:
  ```bash
  # Publish with a title and tags:
  phantom devto post post.md --title "My Post" --tags python webdev

  # Title taken from the file's first "# Heading" (removed from the body):
  phantom devto post post.md --tags "python, ai"

  # With a cover image:
  phantom devto post post.md --tags python --cover ./cover.png

  # Cross-post with a canonical URL:
  phantom devto post post.md --canonical-url https://blog.example.com/my-post

  # Add to a series (created if it doesn't exist yet):
  phantom devto post post.md --series "Automation Diaries"

  # Schedule for later (browser's local timezone):
  phantom devto post post.md --schedule "2026-11-01 09:00"

  # Save as a draft instead of publishing:
  phantom devto post post.md --draft
  ```

* **Direct Python Usage**:
  ```bash
  uv run python devto/post.py <markdown_file> [-t <title>] [--tags <tags>...] [-c <cover>] [--canonical-url <url>] [--series <name>] [--schedule "YYYY-MM-DD HH:MM"] [--draft] [--headless]
  ```

* **Options**:
  - `markdown_file` *(positional)*: Path to the Markdown file with the post body.
  - `-t, --title`: Post title. Defaults to the front matter `title`, else the file's first `# Heading`, which is then removed from the body so it isn't shown twice.
  - `--tags`: Up to 4 tags, space or comma separated. A leading `#` is stripped and tags are lowercased; dev.to only allows letters and numbers.
  - `-c, --cover`: Path to a local cover image (1000:420 works best).
  - `--canonical-url`: Canonical URL if the post was first published elsewhere.
  - `--series`: Series name. Picks the existing series with that name, or creates it. dev.to only shows a series on posts once it has 2+ posts.
  - `--schedule`: Publish later, at `YYYY-MM-DD HH:MM` in the browser's local timezone. Must be in the future.
  - `--draft`: Save as an unpublished draft. The printed URL is the secret preview link.
  - `--headless`: Launch Chrome in headless mode if it isn't already running.

* **Front Matter**:
  The Markdown file may start with a flat front matter block. Its keys are used as defaults and CLI flags override them:
  ```markdown
  ---
  title: My Post
  tags: python, automation
  cover_image: ./cover.png
  canonical_url: https://blog.example.com/my-post
  series: Automation Diaries
  published: false
  ---
  ```
  `cover_image` must be a local path (relative to the Markdown file); `published: false` saves a draft.

* **Features & Workflow**:
  - Discards the editor's autosaved unsaved `/new` form (kept in localStorage) so each run starts from an empty editor. Anything left unsaved in a `/new` tab is lost.
  - Fills in the title, commits each tag, and pastes the Markdown body.
  - Uploads the cover image and waits for the preview.
  - Sets canonical URL, schedule and series in the *Advanced Post Options* modal.
  - Clicks *Publish*, *Schedule* or *Save Draft*, waits for dev.to to redirect to the post, and prints its URL. If dev.to rejects the post, its error message is shown instead.
  - Local images referenced inside the Markdown are not uploaded; use hosted image URLs in the body.

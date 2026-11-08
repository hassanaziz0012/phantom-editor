# Phantom Editor Documentation

Welcome to the documentation for **Phantom Editor**, a comprehensive suite of automation scripts (both deterministic and LLM-based) designed to streamline content creation workflows across multiple social media platforms.

Whether you're processing audio, editing video segments, or publishing to various networks, these tools are built to automate the repetitive parts of the creation pipeline.

---

## 🛠️ Modules & Scripts

The project is structured into several modular directories, each responsible for a distinct part of the media creation and distribution lifecycle:

Every top-level directory is a Python package, and the repo is installed into its venv in editable mode (`uv sync`), so modules import each other as `from metadata.utils import ...` with no `sys.path` changes. Run them through the `phantom` CLI, which loads `.env` once (see `shared/launch.py`). To run a module that has no `phantom` command: `uv run python -m shared.launch <module> [args]`, e.g. `uv run python -m shared.launch youtube_api.fetch_videos`. `./install.sh` links `phantom` into `~/.local/bin`.

The YT Projects folder defaults to `~/Videos/YT Projects`; set `PHANTOM_PROJECTS_DIR` (in `.env` or the shell) to move it. The Chrome profile the browser automations share defaults to `~/Desktop/browser-profiles/p9222`; set `PHANTOM_CDP_PROFILE` to move it.

### 🎬 [Content Pipelines](pipelines.md)
End-to-end orchestration for the 10-stage YouTube content lifecycle and single-pass video masking/trimming.

* **Main Scripts**: [current_pipeline.py](../projects/current_pipeline.py), [process_video.py](../projects/process_video.py) (with the `pipeline/` package), and [newvideo.py](../projects/newvideo.py)

### 🎙️ [Audio Processing](audio-processing.md)
Tools for extracting, clean-up, and normalizing audio tracks.

* **Pipeline**: WAV extraction $\rightarrow$ Deep Filter noise reduction $\rightarrow$ `loudnorm` normalization $\rightarrow$ video track replacement.
* **Main Script**: `process_audio.sh`

### 🎬 [Video Editing & Automation](video-editing.md)
Automation scripts to handle video rendering, splitting, and effects.

* **Main Scripts**: automated subtitle generation, rendering workflows, and timeline parsing.

### 🎞️ [B-roll Templates](broll.md)
Reusable HyperFrames B-roll clips timed to the video's word-level captions from a small cue file.

* **Main Script**: `broll_templates/broll.py` (`phantom broll`)

### 🤖 [Filmora Automation](filmora-automation.md)
Automating timeline composition and video editing workflows in Wondershare Filmora.

### 🔍 [Video Quality Review](review.md)
Inspect final videos for aspect ratio, resolution, and glitchy frames (black screen and freeze frames) prior to distribution.

* **Main Script**: [video_inspector.py](../review/video_inspector.py)

---

## 📢 Platform Integrations & Uploaders

Automatic publishing and uploading scripts to distribute finalized content across platforms:

* 🎥 **[YouTube API](youtube-api.md)**: Upload videos, configure metadata, and manage YouTube uploads.
* 🐦 **[Twitter / X](twitter.md)**: Post scheduling and media tweeting workflows.
* 💼 **[LinkedIn](linkedin.md)**: Share posts and updates automatically.
* 📝 **[Substack](substack.md)**: Post Substack Notes with optional images and video.
* 👩‍💻 **[DEV Community](devto.md)**: Publish, schedule or draft dev.to posts from Markdown files.
* 🤖 **[Reddit](reddit.md)**: Scrape posts/feeds and analyze community sentiment with Groq.

---

## 🚀 Getting Started

To read the details about any script, choose a section from the navigation sidebar or tabs at the top. Every document includes usage flags, requirements, and examples.

---

## 📖 Serving Documentation

You can preview the documentation locally using the `phantom` CLI wrapper:

```bash
phantom docs open
```

This starts the MkDocs development server (if not already running) at `http://127.0.0.1:8000` and automatically opens the home page in your web browser. If the server is already active, it will directly open the browser.


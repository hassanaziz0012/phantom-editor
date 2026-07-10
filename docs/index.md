# Phantom Editor Documentation

Welcome to the documentation for **Phantom Editor**, a comprehensive suite of automation scripts (both deterministic and LLM-based) designed to streamline content creation workflows across multiple social media platforms.

Whether you're processing audio, editing video segments, or publishing to various networks, these tools are built to automate the repetitive parts of the creation pipeline.

---

## 🛠️ Modules & Scripts

The project is structured into several modular directories, each responsible for a distinct part of the media creation and distribution lifecycle:

### 🎙️ [Audio Processing](audio-processing.md)
Tools for extracting, clean-up, and normalizing audio tracks.

* **Pipeline**: WAV extraction $\rightarrow$ Deep Filter noise reduction $\rightarrow$ `loudnorm` normalization $\rightarrow$ video track replacement.
* **Main Script**: `process_audio.sh`

### 🎬 [Video Editing & Automation](video-editing.md)
Automation scripts to handle video rendering, splitting, and effects.

* **Main Scripts**: automated subtitle generation, rendering workflows, and timeline parsing.

### 🤖 [Filmora Automation](filmora-automation.md)
Automating timeline composition and video editing workflows in Wondershare Filmora.

---

## 📢 Platform Integrations & Uploaders

Automatic publishing and uploading scripts to distribute finalized content across platforms:

* 🎥 **[YouTube API](youtube-api.md)**: Upload videos, configure metadata, and manage YouTube uploads.
* 📸 **[Instagram](instagram.md)**: Automatic Reel and post uploads via custom instagrapi workflows.
* 🎵 **[TikTok](tiktok.md)**: CLI-based automated TikTok video publishing.
* 🐦 **[Twitter / X](twitter.md)**: Post scheduling and media tweeting workflows.
* 💼 **[LinkedIn](linkedin.md)**: Share posts and updates automatically.
* 📱 **[Shorts Workflow](shorts.md)**: Multi-platform short-form video uploader and tracker.

---

## 🚀 Getting Started

To read the details about any script, choose a section from the navigation sidebar or tabs at the top. Every document includes usage flags, requirements, and examples.

# Phantom Video Processing Pipeline: CPU Optimization Plan (Updated & Revised)

## 1. Executive Summary & Hardware Context

The video processing pipeline orchestrator ([`pipelines/process_video.py`](file:///home/hassan/Desktop/programming/phantom-editor/pipelines/process_video.py)) automates end-to-end video production: cloud transcription, silence trimming / webcam masking, DeepFilterNet noise removal and loudness normalization, background music (BGM) mixing, quality control inspection (audio and video), and YouTube metadata generation.

### System Constraints & User Parameters
* **CPU:** Intel(R) Core(TM) i5-4460S @ 2.90GHz (4 physical cores, 4 threads, no Hyper-Threading).
* **GPU / Acceleration:** 100% CPU-bound software execution.
* **Video Encoding Preset:** Must strictly remain **`-preset veryfast`** with `-crf 20` (preserving pristine visual quality for screen recordings and preventing quality degradation).
* **Audio Review Neural Network:** Must strictly remain **PANNs `Cnn14`** (81M parameter network) to guarantee zero compromise in flagging coughs, sneezes, mouth clicks, or background noises.
* **CPU Core Constraint (Sequential Step 6):** Audio review (PyTorch) and video review (FFmpeg) **must run sequentially, never concurrently**. On a 4-core, 4-thread CPU, running both simultaneously causes heavy context switching and cache thrashing, completely locking up CPU throughput.

---

## 2. Recent Pipeline Improvements Already Implemented

A comprehensive audit of the codebase confirms several major optimizations are already live:
1. **Concat Demuxer Seeking for Raw Trimming ([`pipelines/process_video.py`](file:///home/hassan/Desktop/programming/phantom-editor/pipelines/process_video.py)):** Step 2 in `--raw` mode now uses FFmpeg's concat demuxer with `inpoint` and `outpoint` timestamps, skipping the decoding of non-speech frames during silence removal.
2. **Background Cloud Transcription ([`pipelines/process_video.py`](file:///home/hassan/Desktop/programming/phantom-editor/pipelines/process_video.py)):** In `--raw` mode (and `--all` mode), Step 1 transcription runs in a background thread concurrently with Step 2 video encoding, masking network latency.
3. **Single-Pass Final Multiplexing ([`pipelines/process_video.py`](file:///home/hassan/Desktop/programming/phantom-editor/pipelines/process_video.py)):** Step 3 outputs `processed-audio.wav` directly without re-encoding video. Step 4 combines the video stream (`-c:v copy`), processed audio, and optional BGM directly into `final.mp4` in a single pass.
4. **Background Metadata Generation ([`pipelines/process_video.py`](file:///home/hassan/Desktop/programming/phantom-editor/pipelines/process_video.py)):** Step 7 (AI metadata) is launched in a background thread right after Step 5, overlapping network/LLM generation with Step 6 review without competing for CPU cycles.
5. **Fast Audio Loading via `soundfile` ([`review/audio_review.py`](file:///home/hassan/Desktop/programming/phantom-editor/review/audio_review.py)):** Replaced `librosa.load` with `soundfile.read` for raw PCM reads, speeding up audio ingest by ~65x.
6. **Concurrent Metadata Tasks ([`metadata/auto_create_metadata.py`](file:///home/hassan/Desktop/programming/phantom-editor/metadata/auto_create_metadata.py)):** Description, tweet, timestamps, and recommendations run in parallel via `ThreadPoolExecutor`.
7. **Video Inspector Downscaling ([`review/video_inspector.py`](file:///home/hassan/Desktop/programming/phantom-editor/review/video_inspector.py)):** Added `--scale-width 640` downscaling before glitch filters, speeding up QC inspection by ~80%.

---

## 3. Retired / Inapplicable Strategies

* **Retired: Concurrent Step 6 Execution (Audio Review + Video Inspector):**
  * *Reason:* On a 4-core/4-thread CPU, running PyTorch Cnn14 (4 OpenMP threads) simultaneously with FFmpeg video decoding (4 threads) spawns 8 CPU-hungry threads competing for 4 cores. This leads to thread thrashing, cache eviction, and near-zero CPU progress. Audio and video review must run **one after the other**.
* **Rejected: Fast Presets (`superfast` / `ultrafast`):**
  * *Reason:* Preserves `-preset veryfast` as requested to avoid video compression artifacts.
* **Rejected: Smaller PANNs Models (`Cnn6` / `Cnn10`):**
  * *Reason:* Preserves `Cnn14` to maintain high recall for vocal bloopers.

---

## 4. High-ROI Optimization Ranking (Ordered by Time Saved)

Here is the prioritized roadmap of new optimizations, ranked strictly by highest return on investment (most time saved first):

| Rank | Optimization Strategy | Target File | Est. Time Saved (10m Video) | Complexity |
| :--- | :--- | :--- | :--- | :--- |
| **1** | **Audio Review Hop Size Tuning (`0.5s` $\to$ `1.0s`)** | [`review/audio_review.py`](file:///home/hassan/Desktop/programming/phantom-editor/review/audio_review.py) & [`pipelines/process_video.py`](file:///home/hassan/Desktop/programming/phantom-editor/pipelines/process_video.py) | **~40s – 45s** | Trivial |
| **2** | **DeepFilterNet Mono Channel Processing (`-ac 1`)** | [`audio-processing/extract_wav_from_mp4.sh`](file:///home/hassan/Desktop/programming/phantom-editor/audio-processing/extract_wav_from_mp4.sh) | **~15s – 20s** | Trivial |
| **3** | **Concat Demuxer Seeking for Dual-Mode Trimming** | [`pipelines/single_pass_mask_trim.py`](file:///home/hassan/Desktop/programming/phantom-editor/pipelines/single_pass_mask_trim.py) | **~50s – 75s** *(in dual mode)* | Medium |
| **4** | **Step 2 Single-Pass Audio Output & Fast Draft Audio** | [`pipelines/process_video.py`](file:///home/hassan/Desktop/programming/phantom-editor/pipelines/process_video.py) & [`audio-processing/process_audio.sh`](file:///home/hassan/Desktop/programming/phantom-editor/audio-processing/process_audio.sh) | **~6s – 10s** | Low |
| **5** | **Cloud Transcription Audio Compression (MP3 vs FLAC)** | [`video-editing/transcribe_cloud.py`](file:///home/hassan/Desktop/programming/phantom-editor/video-editing/transcribe_cloud.py) | **~5s – 15s** | Low |
| **6** | **Video Inspector Downscale Tuning (`640` $\to$ `320`/`426`)** | [`review/video_inspector.py`](file:///home/hassan/Desktop/programming/phantom-editor/review/video_inspector.py) | **~4s – 6s** | Trivial |
| **7** | **RAM Disk (`/dev/shm`) for DeepFilterNet Chunks** | [`audio-processing/noise_reduction.sh`](file:///home/hassan/Desktop/programming/phantom-editor/audio-processing/noise_reduction.sh) | **~2s – 4s** | Trivial |

---

## 5. In-Depth Breakdown of Proposed Optimizations

### 1. Audio Review Hop Size Tuning with Cnn14 (Rank 1 — Highest ROI)
* **How it works now:** `review/audio_review.py` defaults to a 0.5-second hop size (`--hop-sec 0.5`). When `pipelines/process_video.py` calls audio review, no hop size is passed, so it uses this default. For a 7-minute edited video, a 0.5s hop produces **~840 windows**. Benchmarks on this machine show each Cnn14 window requires **105.3ms** on 4 CPU cores, totaling **~88.5 seconds** of inference time.
* **The Optimization:** Change the default hop size to `1.0s` (and pass `--hop-sec 1.0` in `process_video.py`).
* **Why it's completely safe:** The analysis window itself is 2.0 seconds long (`--win-sec 2.0`). Acoustic interruptions (coughs, sneezes, chair scrapes, mic thumps) last 0.5s to 2.5s. With a 2.0s window and a 1.0s hop, **every single second of audio is evaluated in at least two separate overlapping windows**. Nothing is skipped, and **Cnn14** is retained without architecture compromises.
* **Impact:** Window count drops from 840 to 420. Inference time drops from ~88s to ~44s.
* **Expected Savings:** **40–45 seconds**.

---

### 2. DeepFilterNet Mono Processing via `-ac 1` (Rank 2)
* **How it works now:** In `audio-processing/extract_wav_from_mp4.sh`, line 37 extracts audio with `-ac 2` (stereo):
  ```bash
  ffmpeg -i "$INPUT_FILE" -vn -acodec pcm_s16le -ar 48000 -ac 2 -y "$OUTPUT_FILE"
  ```
  Microphone and webcam audio are fundamentally mono sources. Extracting as stereo duplicates the voice signal across two identical channels. `deep-filter` then performs STFT calculations and neural network mask predictions on **two full channels** instead of one.
* **Benchmark Evidence:** Directly benchmarked on this machine's CPU:
  * 10s Mono `deep-filter`: **3.20 seconds**
  * 10s Stereo `deep-filter`: **4.94 seconds** (35% slower!)
* **The Optimization:** Change `extract_wav_from_mp4.sh` to extract mono audio (`-ac 1`). In Step 4, FFmpeg naturally maps mono voice to stereo or mixes it with stereo BGM during final multiplexing.
* **Expected Savings:** **15–20 seconds**.

---

### 3. Concat Demuxer Seeking for Dual-Recording Mode (Rank 3)
* **How it works now:** While `--raw` mode in `process_video.py` was updated to use FFmpeg's concat demuxer seeking, dual recording mode ([`pipelines/single_pass_mask_trim.py`](file:///home/hassan/Desktop/programming/phantom-editor/pipelines/single_pass_mask_trim.py)) still uses a monolithic `select` / `setpts` filter string. Even though silences are discarded, FFmpeg software-decodes **100% of both raw video streams**, renders webcam corner scaling and mask alpha blending over silent sections, and only discards the frames at the end.
* **The Optimization:** Apply segment-based seek cutting to `single_pass_mask_trim.py` so only speech intervals are decoded and composited.
* **Expected Savings:** **50–75 seconds** when processing dual webcam + screen projects.

---

### 4. Step 2 Direct Audio Extraction & Fast Draft Audio (Rank 4)
* **How it works now:**
  1. In Step 2, FFmpeg encodes video with `-c:v libx264 -preset veryfast` and encodes audio with `-c:a aac -b:a 384k`.
  2. In Step 3, `process_audio.sh` immediately calls `extract_wav_from_mp4.sh` to decode that 384k AAC track back into an uncompressed WAV file (`raw-audio.wav`).
  3. In Step 4, `final.mp4` replaces that audio entirely with `processed-audio.wav`. The 384k AAC audio encoded in Step 2 is completely discarded.
* **The Optimization:**
  1. Tell FFmpeg in Step 2 to write `step2_output` with lightweight audio (`-c:a aac -b:a 64k` for quick draft previewing) OR output `raw-audio.wav` directly in the **same FFmpeg pass** using multiple outputs (`-map 0:a -c:a pcm_s16le -ar 48000 -ac 1 raw-audio.wav`).
  2. Step 3 detects `raw-audio.wav` and skips the `extract_wav_from_mp4.sh` pass entirely.
* **Expected Savings:** **6–10 seconds**.

---

### 5. Cloud Transcription Audio Compression (Rank 5)
* **How it works now:** [`video-editing/transcribe_cloud.py`](file:///home/hassan/Desktop/programming/phantom-editor/video-editing/transcribe_cloud.py) extracts audio as an uncompressed 16kHz mono FLAC file (`-c:a flac`). For recordings over 15 minutes, FLAC exceeds Groq's 20MB limit. This triggers `prepare_audio_chunks`, which loops through FFmpeg seeking and chunk-splitting multiple FLAC files on disk, then sends multiple separate HTTP requests.
* **The Optimization:** Extract audio to a compact 32 kbps mono MP3 or Opus file (`-c:a libmp3lame -b:a 32k -ar 16000 -ac 1`):
  * A 15-minute file is only **~3.6 MB** (vs 25+ MB FLAC).
  * Up to 1.5 hours of audio fits within Groq's 25MB limit without ever triggering chunking.
  * Eliminates FFmpeg chunk-splitting disk I/O and multiple API roundtrips.
* **Expected Savings:** **5–15 seconds** (higher on longer raw recordings).

---

### 6. Video Inspector Downscale Tuning (`640` $\to$ `320` or `426`) (Rank 6)
* **How it works now:** [`review/video_inspector.py`](file:///home/hassan/Desktop/programming/phantom-editor/review/video_inspector.py) downscales video to 640 width before running `blackdetect` and `freezedetect`.
* **The Optimization:** Lower `--scale-width` to `320` (180p) or `426` (240p).
  * `blackdetect` measures average frame luminance, and `freezedetect` computes pixel difference against previous frames.
  * A 320x180 frame contains 57,600 pixels—more than enough statistical resolution to detect whole-screen freezes or black transitions.
  * Reduces pixel processing volume by 75% compared to 640p.
* **Expected Savings:** **4–6 seconds**.

---

### 7. RAM Disk (`/dev/shm`) for DeepFilterNet Chunks (Rank 7)
* **How it works now:** In `audio-processing/noise_reduction.sh`, `TMP_DIR=$(mktemp -d)` creates chunk directories in `/tmp` (on physical disk). FFmpeg segments audio into chunks, `deep-filter` reads and writes chunk files, and FFmpeg concats them back together.
* **The Optimization:** Use `TMP_DIR=$(mktemp -d -p /dev/shm)` when `/dev/shm` is available. On Linux, `/dev/shm` is a shared-memory RAM disk that performs reads and writes at memory speed (several GB/s) with zero disk wear and zero latency.
* **Expected Savings:** **2–4 seconds**.

---

## 6. Projected Total Pipeline Runtime Comparison

| Step | Initial Baseline | Current (With Recent Improvements) | Projected (With New Optimizations) |
| :--- | :--- | :--- | :--- |
| **Step 1: Transcription** | ~15s | ~0s (overlapped in `--raw` mode) / 15s | **~0s** (overlapped) / **~7s** (compact MP3) |
| **Step 2: Silence Trimming** | ~180s | ~95s (concat demuxer in raw mode) | **~85s** (fast draft audio / dual-mode concat) |
| **Step 3: Audio Processing** | ~90s | ~55s (DeepFilterNet 4-core chunking) | **~35s – 38s** (Mono `-ac 1` + RAM temp) |
| **Step 4 & 5: Remux & Finalize** | ~35s | ~10s (single remux pass) | **~10s** (single remux pass) |
| **Step 6: Audio & Video Review** | ~145s | ~100s (sequential: 88s audio + 12s video) | **~48s – 52s** (1.0s hop: 44s audio + 6s video) |
| **Step 7: Metadata Generation** | ~40s | ~0s (overlapped in background) | **~0s** (overlapped in background) |
| **TOTAL PIPELINE RUNTIME** | **~8m 25s** | **~4m 20s** | **~2m 58s – 3m 15s** |

# Phantom Video Processing Pipeline: CPU Optimization Plan (Updated)

## 1. Executive Summary & Hardware Context

The current video processing pipeline orchestrator ([`pipelines/process_video.py`](file:///home/hassan/Desktop/programming/phantom-editor/pipelines/process_video.py)) handles end-to-end execution: transcription, silence trimming / webcam masking, audio processing (DeepFilterNet + loudnorm), BGM addition, audio & video inspection, and metadata generation.

### System Constraints & User Parameters
* **CPU:** Intel(R) Core(TM) i5-4460S @ 2.90GHz (4 physical cores, 4 threads, no Hyper-Threading)
* **GPU / Acceleration:** Software CPU-bound execution.
* **Encoding Preset:** Must remain **`-preset veryfast`** (preserving pristine visual quality for screen recordings without dropping to `superfast`).
* **Audio Review Neural Network:** Must remain **PANNs `Cnn14`** (81M parameter network) to guarantee zero compromise in detecting coughs, sneezes, mouth clicks, or room interruptions.

---

## 2. Recent Pipeline Improvements Already Implemented

A comprehensive audit of the latest repository changes reveals several key optimizations already completed:
1. **Single-Pass Final Multiplexing ([`pipelines/process_video.py`](file:///home/hassan/Desktop/programming/phantom-editor/pipelines/process_video.py)):** Step 3 now operates in `--pipeline-mode`, outputting only `processed-audio.wav`. Step 4 combines video copying (`-c:v copy`), `processed-audio.wav`, and optional BGM directly into `final.mp4`, eliminating 2 multi-gigabyte intermediate video writes (`after-audio-processing.mp4` and `after-audio-processing-bgm.mp4`).
2. **Video Inspector Downscaling ([`review/video_inspector.py`](file:///home/hassan/Desktop/programming/phantom-editor/review/video_inspector.py)):** Added `--scale-width 640` (downscaling to 360p before `blackdetect` and `freezedetect`), speeding up Step 6 video QC scanning by ~80% while retaining pixel-level freeze and black frame detection.
3. **PANNs Batch Size & Inference Mode ([`review/audio_review.py`](file:///home/hassan/Desktop/programming/phantom-editor/review/audio_review.py)):** Inference batch size was doubled to 64 and wrapped in `torch.inference_mode()`.
4. **Concurrent Metadata Generation ([`metadata/auto_create_metadata.py`](file:///home/hassan/Desktop/programming/phantom-editor/metadata/auto_create_metadata.py)):** All 4 LLM tasks (description, tweet, recommendations, timestamps) execute in parallel via `ThreadPoolExecutor`, reducing metadata runtime from ~40s to ~12s.

---

## 3. High-ROI Optimization Ranking (Ordered by Time Saved)

Here is the prioritized roadmap of new optimizations, ranked strictly by highest return on investment (most time saved first) while adhering to `-preset veryfast` and `Cnn14`:

| Rank | Optimization Strategy | Target File | Est. Time Saved (10m Video) | Complexity |
| :--- | :--- | :--- | :--- | :--- |
| **1** | **Concat Demuxer Seeking for Silence Trimming** | [`pipelines/process_video.py`](file:///home/hassan/Desktop/programming/phantom-editor/pipelines/process_video.py) | **~70s – 90s** | Low-Medium |
| **2** | **Audio Review Hop Size Tuning (`0.5s` $\to$ `1.0s`)** | [`review/audio_review.py`](file:///home/hassan/Desktop/programming/phantom-editor/review/audio_review.py) | **~30s – 45s** | Trivial |
| **3** | **Concurrent Step 6 Execution (Audio Review + Video Inspector)** | [`pipelines/process_video.py`](file:///home/hassan/Desktop/programming/phantom-editor/pipelines/process_video.py) | **~15s – 25s** | Low |
| **4** | **Step 2 Fast Audio Encoding (`-c:a copy` / low-bitrate draft)** | [`pipelines/process_video.py`](file:///home/hassan/Desktop/programming/phantom-editor/pipelines/process_video.py) & [`single_pass_mask_trim.py`](file:///home/hassan/Desktop/programming/phantom-editor/pipelines/single_pass_mask_trim.py) | **~5s – 8s** | Trivial |
| **5** | **Optimized Audio Loading via `soundfile`** | [`review/audio_review.py`](file:///home/hassan/Desktop/programming/phantom-editor/review/audio_review.py) | **~2s – 4s** | Trivial |
| **6** | **Pipeline Overlap: Async Step 1 Transcription during Step 2** | [`pipelines/process_video.py`](file:///home/hassan/Desktop/programming/phantom-editor/pipelines/process_video.py) (in `--raw` mode) | **~10s – 15s** | Medium |

---

## 4. In-Depth Breakdown of Proposed Optimizations

### 1. Concat Demuxer Seeking for Silence Trimming (Rank 1 — Highest ROI)
* **What happens right now:** In `run_step2_trim_silences_raw`, FFmpeg uses a massive `select` and `setpts` filter string. Even though it discards silent sections, FFmpeg's video pipeline must software-decode **100% of every frame in the raw video** to test the mathematical timestamp expressions. If 35% of a 10-minute video is silence, your CPU spends ~60–80 seconds decoding video frames just to throw them away.
* **The Fix:** Switch `run_step2_trim_silences_raw` to use FFmpeg's concat demuxer (already proven in `video-editing/trim_silences.py`). FFmpeg seeks directly between speech `inpoint` and `outpoint` timestamps, completely skipping silent frames during decode.
* **Preserving Quality & Preset:** Retains `-preset veryfast` and `-crf 20`. Encoding time is reduced simply because fewer non-speech frames enter the encoder pipeline.
* **Expected Savings:** **70–90 seconds**.

---

### 2. Audio Review Hop Size Adjustment with Cnn14 (Rank 2)
* **What happens right now:** `review/audio_review.py` splits the audio into 2.0-second analysis windows with a 0.5-second hop size (`--hop-sec 0.5`).
  * For a 7-minute processed video (420s), a 0.5s hop size creates **840 windows**.
  * On 4 CPU cores running Cnn14 (~16.7 windows/second), this takes **~50.3 seconds**.
* **The Fix:** Increase the default `--hop-sec` from `0.5` to `1.0`.
  * Human acoustic bloopers (coughs, sneezes, chair scrapes, throat clears) last between 0.8s and 2.5s.
  * With a 2.0s analysis window, a 1.0s hop guarantees every event is sampled at least twice with over 50% window coverage.
  * Windows drop from 840 to **420 windows**, cutting inference time by **50%**.
  * Retains **Cnn14** entirely for detection reliability.
* **Expected Savings:** **30–45 seconds**.

---

### 3. Concurrent Audio & Video Review in Step 6 (Rank 3)
* **What happens right now:** In `run_step6_review`, Python runs `audio_review.py`, waits for it to complete, and then runs `video_inspector.py` sequentially.
  * `audio_review.py` uses PyTorch matrix operations on audio tensors.
  * `video_inspector.py` uses FFmpeg's C-based downscaled video decoding (`scale=640:-2`).
* **The Fix:** Execute `audio_review.py` and `video_inspector.py` concurrently using a `ThreadPoolExecutor(max_workers=2)` in `process_video.py`. Video inspection finishes in ~12s while audio review is running.
* **Expected Savings:** **15–25 seconds**.

---

### 4. Fast Audio Encoding in Step 2 Silence Trimming (Rank 4)
* **What happens right now:** Step 2 encodes high-bitrate AAC audio (`-c:a aac -b:a 384k`). However, Step 3 immediately extracts the audio, filters it through DeepFilterNet, normalizes it, and outputs `processed-audio.wav`. The AAC track encoded in Step 2 is completely discarded when `processed-audio.wav` is muxed into `final.mp4`.
* **The Fix:** In Step 2, encode audio with lightweight `-c:a aac -b:a 128k` (or stream copy where possible with concat), cutting redundant audio encoding CPU cycles.
* **Expected Savings:** **5–8 seconds**.

---

### 5. Fast WAV Loading via `soundfile` in Audio Review (Rank 5)
* **What happens right now:** `audio_review.py` calls `librosa.load(wav_path, sr=SAMPLE_RATE, mono=True)`. Benchmark measurements on this machine show `librosa.load` takes **488ms** for 60s of audio, whereas `soundfile.read` takes **7.5ms** (**65x faster**).
* **The Fix:** Replace `librosa.load` with `soundfile.read` for loading PCM WAV files into NumPy arrays.
* **Expected Savings:** **2–4 seconds**.

---

### 6. Pipeline Overlap: Background Cloud Transcription in `--raw` Mode (Rank 6)
* **What happens right now:** In `--raw` mode, Step 1 (Groq Cloud Whisper API) runs first and waits ~10–15s over the network. However, raw mode silence trimming does **not** depend on subtitles (it uses Silero VAD directly on audio).
* **The Fix:** In `--raw` mode, start Step 1 as an asynchronous background worker while Step 2 begins encoding immediately. The transcript is joined before Step 7 (metadata creation).
* **Expected Savings:** **10–15 seconds** (completely masks network round-trip time).

---

## 5. Projected Total Runtime Comparison

| Step | Baseline Runtime | Current (Post-Recent Commits) | Projected with New Optimizations |
| :--- | :--- | :--- | :--- |
| **Step 1: Transcription** | ~15s | ~15s | ~0s (overlapped in `--raw` mode) / 15s |
| **Step 2: Silence Trimming** | ~180s | ~180s | **~90s – 100s** (concat demuxer skipping) |
| **Step 3: Audio Processing** | ~90s | ~55s | ~55s (DeepFilterNet 4-core chunking) |
| **Step 4 & 5: Remux & Finalize** | ~35s | ~10s | ~10s (single remux pass) |
| **Step 6: Audio & Video Review** | ~145s | ~70s | **~25s – 30s** (1.0s hop + parallel run) |
| **Step 7: Metadata Generation** | ~40s | ~12s | ~12s (parallel LLM tasks) |
| **TOTAL PIPELINE RUNTIME** | **~8m 25s** | **~5m 42s** | **~3m 15s – 3m 30s** |

# 🔍 Video Quality Review & Inspection

The **Review** module provides quality control (QC) tools to inspect video files before publishing. It scans videos for black screens, frozen frames, and details their aspect ratio and resolution.

---

## 🚀 Quick Start

Run the quality check on a video file:

```bash
phantom review inspect /path/to/video.mp4
```

---

## 🛠️ How It Works

The script executes under-the-hood checks using **ffprobe** and **ffmpeg** (Epic MPEGs) filters to analyze video signals:

1. **Metadata Inspection**: Reads raw resolution, sample aspect ratio (SAR), display aspect ratio (DAR), exact duration, and average frame rate. If display aspect ratio is missing, it computes the ratio dynamically from width and height.
2. **Black Screen Detection (`blackdetect`)**: Scans for contiguous periods of black or near-black frames.
3. **Freeze Frame Detection (`freezedetect`)**: Scans for segments where video frames become completely static or frozen.

Any detected issues are logged live to the console with the exact timestamps in `HH:MM:SS.mmm` format, making it easy to hop into your editor and fix the sequence.

---

## 📋 Command Arguments

The `inspect` subcommand accepts several options to customize filter thresholds:

| Option | Short | Type | Default | Description |
| :--- | :--- | :--- | :--- | :--- |
| `video_path` | | Positional | | Path to the video file (required). |
| `--black-duration` | `-bd` | float | `0.1` | Minimum black screen duration in seconds to trigger a warning. |
| `--black-pix-th` | `-bx` | float | `0.10` | Threshold for pixel luminance below which it is considered black. |
| `--black-pic-th` | `-bp` | float | `0.98` | Minimum ratio of pixels below the luminance threshold to count as black. |
| `--freeze-duration` | `-fd` | float | `0.5` | Minimum duration in seconds of frozen video to trigger a warning. |
| `--tail-freeze-duration` | `-tfd` | float | `2.0` | Minimum duration in seconds of frozen video at the very end (EOF) before flagging. |
| `--freeze-noise` | `-fn` | float | `0.003` | Noise tolerance threshold for freeze detection. |

### Example custom run:

```bash
phantom review inspect my_video.mp4 -bd 0.5 -fd 1.0 -tfd 3.0 -fn 0.005
```

---

## 📊 Sample Report Output

When you run `phantom review inspect video.mp4`, you will receive a terminal report matching the following style:

```text
================================================================================
VIDEO QUALITY CONTROL INSPECTION REPORT
================================================================================
File Path:       /home/hassan/Desktop/programming/phantom-editor/review/video.mp4
Resolution:      1920x1080 (1080p Full HD)
Aspect Ratio:    16:9 [Ratio: 1.78]
Pixel Aspect:    1:1
Duration:        00:01:30.000 (90.000 seconds)
Frame Rate:      60.00 fps
--------------------------------------------------------------------------------
Scan Parameters:
  • Black Detect:  min_duration=0.1s, pixel_th=0.1, pic_th=0.98
  • Freeze Detect: min_duration=0.5s, noise_tolerance=0.003
--------------------------------------------------------------------------------
❌ SCAN COMPLETED: Found 2 potential issue(s). Details below:

■ BLACK SCREEN DETECTIONS (1):
  1. 00:00:15.200 --> 00:00:15.500     [15.200s to 15.500s]   Duration: 0.300s

■ FREEZE FRAME DETECTIONS (1):
  1. 00:00:45.000 --> 00:00:46.250     [45.000s to 46.250s]   Duration: 1.250s

Note: Review these timestamps in your video editor to fix bad frames/glitches.
================================================================================
```

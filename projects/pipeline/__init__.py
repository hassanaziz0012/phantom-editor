"""Full video processing pipeline (``phantom projects process``).

Chains transcription, silence trimming / webcam masking, audio processing,
final encode (with optional BGM), verification, review and metadata generation.
Entry point: ``projects/process_video.py`` -> :func:`pipeline.cli.main`.
"""

import sys
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PIPELINE_DIR.parent.parent
VIDEO_EDITING_DIR = REPO_ROOT / "video-editing"

# The shared helpers (utils, trim_silences, auto_attach_webcam_mask) live in
# video-editing/, which is not a package, so it has to be on sys.path before
# any pipeline module imports them.
if str(VIDEO_EDITING_DIR) not in sys.path:
    sys.path.insert(0, str(VIDEO_EDITING_DIR))

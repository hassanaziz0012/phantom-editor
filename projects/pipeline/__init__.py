"""Full video processing pipeline (``phantom projects process``).

Chains transcription, silence trimming, audio processing, final encode (with
optional BGM), verification, review and metadata generation for a single
pre-composed recording (e.g. OBS).
Entry point: ``projects/process_video.py`` -> :func:`projects.pipeline.cli.main`.
"""

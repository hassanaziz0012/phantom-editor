# Kokoro TTS

This directory contains scripts for generating Text-to-Speech (TTS) audio using a local instance of the Kokoro-82M model.

## Model
* **[Kokoro-82M/](../kokoro-tts/Kokoro-82M)**: A lightweight, open-weight TTS model (82M parameters) that delivers high-quality, fast, and cost-efficient speech synthesis.

## Scripts

### [demo.py](../kokoro-tts/demo.py)
A basic demonstration script that loads the local model and voice, synthesizes sample text, and outputs each text segment as an individual WAV file (e.g., `0.wav`, `1.wav`).
* **Usage**: `python kokoro-tts/demo.py`

### [makesound.py](../kokoro-tts/makesound.py)
Synthesizes the input text and plays the audio directly through the system speakers.
* **Usage**: `python kokoro-tts/makesound.py "<text>"`
* **Requirements**: `kokoro`, `sounddevice`, `soundfile`, `torch`

### [savesound.py](../kokoro-tts/savesound.py)
Synthesizes the input text and saves the combined audio as a single WAV file at a 24 kHz sample rate.
* **Usage**: `python kokoro-tts/savesound.py "<text>" [--output-dir <directory>]`
* **Default Output**: `<first_20_chars_of_text>_<timestamp>.wav` in the current working directory.
* **Requirements**: `kokoro`, `soundfile`, `torch`

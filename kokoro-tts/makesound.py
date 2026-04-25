# /// script
# requires-python = ">=3.9"
# dependencies = [
#     "kokoro",
#     "sounddevice",
#     "soundfile",
#     "torch",
# ]
# ///

import os
import argparse
import numpy as np
from pathlib import Path
from kokoro import KModel, KPipeline
import sounddevice as sd
import torch

def play_text(text: str):
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    
    # Resolve paths relative to this script so it works from anywhere
    script_dir = Path(__file__).parent.resolve()
    base_dir = script_dir / 'Kokoro-82M'
    
    config_path = base_dir / 'config.json'
    model_path = base_dir / 'kokoro-v1_0.pth'
    voice_path = base_dir / 'voices' / 'af_heart.pt'
    
    # Fallback to the relative paths used in demo.py just in case
    if not config_path.exists():
        config_path = Path('kokoro-tts/Kokoro-82M/config.json')
        model_path = Path('kokoro-tts/Kokoro-82M/kokoro-v1_0.pth')
        voice_path = Path('kokoro-tts/Kokoro-82M/voices/af_heart.pt')
        
    print(f"Loading Kokoro model on {device}...")
    model = KModel(config=str(config_path), model=str(model_path), repo_id='hexgrad/Kokoro-82M').to(device).eval()
    pipeline = KPipeline(lang_code='a', model=model, repo_id='hexgrad/Kokoro-82M')
    voice = torch.load(str(voice_path), weights_only=True).to(device)

    print("Synthesizing audio...")
    generator = pipeline(text, voice=voice)
    
    # Collect all generated audio segments
    audio_segments = []
    for i, (gs, ps, audio) in enumerate(generator):
        audio_segments.append(audio)
        
    if not audio_segments:
        print("No audio was generated.")
        return
        
    # Concatenate all parts to play continuously
    full_audio = np.concatenate(audio_segments)
    
    print("Playing audio directly...")
    # Kokoro outputs audio at a 24000Hz sample rate
    sd.play(full_audio, 24000)
    sd.wait()
    print("Playback finished.")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Convert text to audio using Kokoro and play it directly.")
    parser.add_argument("text", type=str, help="The text to synthesize and play")
    args = parser.parse_args()
    
    play_text(args.text)

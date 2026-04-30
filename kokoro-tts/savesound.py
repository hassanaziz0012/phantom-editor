# /// script
# requires-python = ">=3.9"
# dependencies = [
#     "kokoro",
#     "soundfile",
#     "torch",
# ]
# ///

import os
import argparse
import numpy as np
import soundfile as sf
import time
from pathlib import Path
from kokoro import KModel, KPipeline
import torch

def save_text_to_audio(text: str, output_dir: str):
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
        
    # Concatenate all parts
    full_audio = np.concatenate(audio_segments)
    
    # Create the output directory if it doesn't exist
    out_dir_path = Path(output_dir).resolve()
    out_dir_path.mkdir(parents=True, exist_ok=True)
    
    # Generate a filename
    safe_text = "".join(c if c.isalnum() else "_" for c in text[:20]).strip("_")
    if not safe_text:
        safe_text = "audio"
    
    timestamp = int(time.time())
    filename = f"{safe_text}_{timestamp}.wav"
    output_path = out_dir_path / filename
    
    print(f"Saving audio to {output_path}...")
    # Kokoro outputs audio at a 24000Hz sample rate
    sf.write(str(output_path), full_audio, 24000)
    print("Done!")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Convert text to audio using Kokoro and save it to a file.")
    parser.add_argument("text", type=str, help="The text to synthesize")
    parser.add_argument("--output-dir", type=str, default=os.getcwd(), help="The directory to save the audio file in. Defaults to the current working directory.")
    args = parser.parse_args()
    
    save_text_to_audio(args.text, args.output_dir)

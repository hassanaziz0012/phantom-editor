from kokoro import KModel, KPipeline
import soundfile as sf
import torch

# Load the model and voice locally from the Kokoro-82M folder
device = 'cuda' if torch.cuda.is_available() else 'cpu'
model = KModel(config='kokoro-tts/Kokoro-82M/config.json', model='kokoro-tts/Kokoro-82M/kokoro-v1_0.pth').to(device).eval()
pipeline = KPipeline(lang_code='a', model=model)
voice = torch.load('kokoro-tts/Kokoro-82M/voices/af_heart.pt', weights_only=True).to(device)

text = '''
[Kokoro](/kˈOkəɹO/) is an open-weight TTS model with 82 million parameters. 
Despite its lightweight architecture, it delivers comparable quality to 
larger models while being significantly faster and more cost-efficient. With 
Apache-licensed weights, [Kokoro](/kˈOkəɹO/) can be deployed anywhere from 
production environments to personal projects.
'''
generator = pipeline(text, voice=voice)
for i, (gs, ps, audio) in enumerate(generator):
    print(i, gs, ps)
    sf.write(f'{i}.wav', audio, 24000)

import os
import tempfile
from services.llm_client import get_client


def text_to_speech(user, text: str) -> str:
    """Returns path to a .ogg file"""
    client = get_client(user)
    voice = user.tts_voice or "alloy"

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as mp3_file:
        mp3_path = mp3_file.name

    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as ogg_file:
        ogg_path = ogg_file.name

    # Generate TTS
    response = client.audio.speech.create(
        model="tts-1",
        voice=voice,
        input=text[:4096],  # Telegram voice limit safety
    )
    response.stream_to_file(mp3_path)

    # Convert to ogg/opus for Telegram
    os.system(f"ffmpeg -i {mp3_path} -c:a libopus {ogg_path} -y -loglevel quiet")
    os.remove(mp3_path)

    return ogg_path

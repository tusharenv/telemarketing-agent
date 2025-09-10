import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    TWILIO_PHONE_NUMBER: str = os.getenv("TWILIO_PHONE_NUMBER")
    TWILIO_AUTH_TOKEN: str = os.getenv("TWILIO_AUTH_TOKEN")
    TWILIO_ACCOUNT_SID: str = os.getenv("TWILIO_ACCOUNT_SID")

    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY")
    DEEPGRAM_API_KEY: str = os.getenv("DEEPGRAM_API_KEY")
    ELEVENLABS_API_KEY: str = os.getenv("ELEVENLABS_API_KEY")
    CARTESIA_API_KEY: str = os.getenv("CARTESIA_API_KEY")

settings = Settings()

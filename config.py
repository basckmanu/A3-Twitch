import os
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TOKEN_TWITCH")
CHANNEL = os.getenv("CHANNEL")



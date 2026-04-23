import os

from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TOKEN_TWITCH")
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")

# CHANNELS : liste de noms textuels → pour TwitchIO
CHANNELS = [c.strip() for c in os.getenv("CHANNELS", "").split(",") if c.strip()]

# CHANNEL_ID : liste d'IDs numériques → pour BTTV/FFZ/7TV
CHANNEL_ID = [c.strip() for c in os.getenv("CHANNEL_ID", "").split(",") if c.strip()]

import os

from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TOKEN_TWITCH")
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_CHANNEL_ID = os.getenv("DISCORD_CHANNEL_ID")

_REQUIRED = ["TOKEN", "CLIENT_ID", "CLIENT_SECRET", "DISCORD_BOT_TOKEN", "DISCORD_CHANNEL_ID"]
_missing = [name for name in _REQUIRED if not globals().get(name)]
if _missing:
    raise EnvironmentError(f"Missing required env vars: {', '.join(_missing)}")

# CHANNELS : liste de noms textuels → pour TwitchIO
CHANNELS = [c.strip() for c in os.getenv("CHANNELS", "").split(",") if c.strip()]

# CHANNEL_ID : liste d'IDs numériques → pour BTTV/FFZ/7TV
CHANNEL_ID = [c.strip() for c in os.getenv("CHANNEL_ID", "").split(",") if c.strip()]

# src/a3/config.py
#
# Charge les variables d'environnement depuis .env et exporte la config globale.
# Utilisé par tous les modules du projet.

import os

from dotenv import load_dotenv

load_dotenv()

# Privacy / RGPD — doit être chargé AVANT le check _REQUIRED
A3_HASH_SALT = os.getenv("A3_HASH_SALT", "")

TOKEN = os.getenv("TOKEN_TWITCH")
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_CHANNEL_ID = os.getenv("DISCORD_CHANNEL_ID")

# Base de données (HeidiSQL / MySQL / MariaDB)
DB_HOST = os.getenv("DB_HOST", "localhost")
try:
    DB_PORT = int(os.getenv("DB_PORT", "3306"))
except ValueError:
    DB_PORT = 3306
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "a3_db")
DB_SSLMODE = os.getenv("DB_SSLMODE", "prefer")  # only used by PostgresHandler

_REQUIRED = ["TOKEN", "CLIENT_ID", "CLIENT_SECRET", "DISCORD_BOT_TOKEN", "DISCORD_CHANNEL_ID", "A3_HASH_SALT"]
_missing = [name for name in _REQUIRED if not globals().get(name)]
if _missing:
    raise EnvironmentError(f"Missing required env vars: {', '.join(_missing)}")

# CHANNELS : liste de noms textuels → pour TwitchIO
CHANNELS = [c.strip() for c in os.getenv("CHANNELS", "").split(",") if c.strip()]
if not CHANNELS:
    raise EnvironmentError("Missing required env var: CHANNELS (comma-separated Twitch channel names)")

CHANNEL_ID = [c.strip() for c in os.getenv("CHANNEL_ID", "").split(",") if c.strip()]

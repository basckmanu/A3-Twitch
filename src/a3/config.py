# src/a3/config.py
#
# Charge les variables d'environnement depuis .env et exporte la config globale.
# Utilisé par tous les modules du projet.

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _resolve_base_dir() -> Path:
    """Répertoire de base pour tous les fichiers générés (clips, logs, decisions, cache).

    Priorité : A3_BASE_DIR (override explicite) > /app (Docker) > cwd (dev/install pip).

    Centralisé ici pour que streamCapture.py, decisions.py et mainRendererTwitch.py
    utilisent tous la MÊME racine — chacun calculait auparavant sa propre base
    (l'un via cwd, les deux autres via `Path(__file__).resolve().parents[3]`, qui
    résout vers src/ et non la racine du projet), désynchronisant silencieusement
    où les clips sont écrits (clips_output/) de là où la review et le nettoyage
    par rétention les cherchent (src/clips/) — résultat observé : les previews
    et clips non reviewés ne sont jamais nettoyés (534 Mo accumulés sur 3+ mois)."""
    if env_base := os.getenv("A3_BASE_DIR"):
        return Path(env_base)
    if Path("/app").exists():
        return Path("/app")
    return Path.cwd()


BASE_DIR: Path = _resolve_base_dir()

# Privacy / RGPD — doit être chargé AVANT le check _REQUIRED
A3_HASH_SALT = os.getenv("A3_HASH_SALT", "")

TOKEN = os.getenv("TOKEN_TWITCH")
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_CHANNEL_ID = os.getenv("DISCORD_CHANNEL_ID")

# Base de données (PostgreSQL — seul DB_TYPE supporté, voir structuredLogger.py::_make_db_handler)
DB_HOST = os.getenv("DB_HOST", "localhost")
try:
    DB_PORT = int(os.getenv("DB_PORT", "5432"))
except ValueError:
    DB_PORT = 5432
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "a3_db")
DB_SSLMODE = os.getenv("DB_SSLMODE", "prefer")

_REQUIRED = ["TOKEN", "CLIENT_ID", "CLIENT_SECRET", "DISCORD_BOT_TOKEN", "DISCORD_CHANNEL_ID", "A3_HASH_SALT"]
_missing = [name for name in _REQUIRED if not globals().get(name)]
if _missing:
    raise EnvironmentError(f"Missing required env vars: {', '.join(_missing)}")

# CHANNELS : liste de noms textuels → pour TwitchIO
CHANNELS = [c.strip() for c in os.getenv("CHANNELS", "").split(",") if c.strip()]
if not CHANNELS:
    raise EnvironmentError("Missing required env var: CHANNELS (comma-separated Twitch channel names)")

CHANNEL_ID = [c.strip() for c in os.getenv("CHANNEL_ID", "").split(",") if c.strip()]

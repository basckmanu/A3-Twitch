# syntax=docker/dockerfile:1
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# streamlink via pip pour avoir la dernière version (v8+)
RUN pip install --no-cache-dir streamlink

WORKDIR /app

# Copie pyproject.toml d'abord pour profiter du cache Docker sur les deps
COPY pyproject.toml .
COPY src/ ./src/

# Installe le package a3 et toutes ses dépendances
RUN pip install --no-cache-dir .

# Fichier de blacklist (mots à ignorer)
COPY blacklist_mots.json .

# Crée les dossiers runtime (layout clips/{channel}/{validated,highlights,rejected} —
# les anciens dossiers à plat clips_validated/clips_highlights/clips_rejected ne sont
# plus utilisés par le code depuis la refonte BASE_DIR)
RUN mkdir -p logs buffer_segments clips_output clips decisions cache

COPY docker/start.sh /start.sh
RUN chmod +x /start.sh

ENTRYPOINT ["/start.sh"]

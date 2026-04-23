# syntax=docker/dockerfile:1
FROM python:3.11-slim

# Installer les dépendances système (streamlink + ffmpeg)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    streamlink \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copie uniquement les dépendances Python d'abord (pour le cache Docker)
COPY src/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copie le code source
COPY src/ ./src/

# Crée les dossiers nécessaires au runtime
RUN mkdir -p logs buffer_segments clips_output clips_validated clips_highlights clips_rejected decisions cache

# Wrapper start
COPY docker/start.sh /start.sh
RUN chmod +x /start.sh

ENTRYPOINT ["/start.sh"]

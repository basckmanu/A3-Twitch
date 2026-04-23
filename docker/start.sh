#!/bin/bash
set -e

echo "[A3] Démarrage du conteneur..."

# Active le virtualenv si présent
if [ -f "/app/.venv/bin/activate" ]; then
    source /app/.venv/bin/activate
fi

# Compile les assets Python
python -m compileall src/ -q || true

echo "[A3] Lancement de A3..."
exec python -m a3.main "$@"

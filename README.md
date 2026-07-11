# A3 — Twitch Clip Detector

A3 surveille le chat Twitch en temps réel, détecte les moments de hype via plusieurs filtres
adaptatifs, capture automatiquement des clips vidéo et les envoie sur Discord pour une revue
humaine (garder / highlight / supprimer).

## Architecture

```
TwitchBot (mainTwitch.py)
├── Watcher (mainWatcherTwitch.py)
│   └── Filtres: MessageRate, UniqueAuthors, Emotions, EmoteDensity, Repetition, ClipActivity
│       └── FiltreAdaptatif (algorithme de Welford pour seuils adaptatifs)
├── Brain (mainBrainTwitch.py)
│   ├── Agrège les scores des filtres (pondération)
│   ├── Gère la logique de décision (fenêtres de merge, cooldowns)
│   └── Déclenche StreamCapture au-delà du seuil
├── Renderer (mainRendererTwitch.py)
│   └── Bot Discord — envoie les clips avec boutons garder/highlight/supprimer
├── StreamCapture (streamCapture.py)
│   └── streamlink + ffmpeg pour la capture et le découpage vidéo
└── DecisionLogger (decisions.py)
    └── Logs de session JSON dans decisions/, DB PostgreSQL pour l'historique structuré
```

## Prérequis

- Python >= 3.12
- PostgreSQL (voir [Base de données](#base-de-données))
- [streamlink](https://streamlink.github.io/) et [ffmpeg](https://ffmpeg.org/) dans le PATH
- Un bot Discord (token + salon dédié)
- Des identifiants Twitch API (Client ID / Secret) + un token OAuth de chat

## Installation

```bash
# Environnement virtuel
python -m venv .venv
source .venv/Scripts/activate   # Windows Git Bash
# ou : .venv\Scripts\Activate.ps1  (PowerShell)

pip install -e ".[dev]"

# Config
cp .env.example .env
# remplir .env : tokens Twitch/Discord, identifiants DB, A3_HASH_SALT
```

`A3_HASH_SALT` doit être une valeur aléatoire forte et unique par déploiement (sert à
pseudonymiser les pseudos Twitch et les reviewers Discord avant stockage en DB — voir
`.env.example` pour le détail RGPD). Génère-la avec :

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

## Base de données

PostgreSQL est le seul moteur supporté (`DB_TYPE=postgres`). Le schéma canonique — celui qui
reflète l'état réel de la DB de prod (tables, index, triggers, vues matérialisées,
partitionnement mensuel de `filter_events`/`stream_events`) — vit dans `sql/schema_postgresql.sql`,
régénéré via :

```bash
pg_dump -h <host> -U <user> -d a3_db --schema-only --no-owner --no-privileges -f sql/schema_postgresql.sql
```

Pour créer une base vierge à partir de ce schéma :

```bash
createdb a3_db
psql -h <host> -U <user> -d a3_db -f sql/schema_postgresql.sql
```

Alternative : lancer le bot une fois avec une DB vide suffit aussi — `PostgresHandler._creer_tables()`
crée les tables de base au démarrage (mais pas les vues matérialisées ni le partitionnement, qui
sont posés manuellement — utiliser le fichier `.sql` pour une réplication fidèle).

## Lancer le bot

```bash
python -m a3.main
```

## Tests / dashboard live des filtres

```bash
pytest
python -m a3.Twitch.tests.test_filtres_live
```

## Lint / typage

```bash
ruff check src/
mypy src/
```

## Docker

```bash
docker compose -f docker/docker-compose.yml up
```

## Fusion avec une autre partie de l'app

Si tu intègres ce repo avec une autre application (ex: un service développé séparément) :

- Le schéma DB canonique (`sql/schema_postgresql.sql`) est la référence à importer côté DB —
  ne pas repartir des anciennes copies (root, `docs/`), elles ont été supprimées car divergentes
  et obsolètes.
- `.env` n'est **jamais** commité (voir `.gitignore`) — chaque déploiement a ses propres secrets
  et son propre `A3_HASH_SALT`.
- Les pseudos Twitch et identifiants Discord sont stockés hashés (SHA-256 + salt), pas en clair.

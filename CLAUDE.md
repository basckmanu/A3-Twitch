# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A3 is a Twitch clip detection and capture system. It monitors Twitch chat in real-time, uses multiple adaptive filters to detect "hype" moments, automatically captures video clips, and sends them to Discord for human review (keep/highlight/delete).

## Running the Bot

```bash
# Activate virtual environment
source .venv/Scripts/activate

# Run the main bot
python -m a3.main

# Run filter tests / live dashboard
python -m a3.Twitch.tests.test_filtres_live
```

## Architecture

```
TwitchBot (mainTwitch.py)
├── Watcher (mainWatcherTwitch.py)
│   └── Filtres: MessageRate, UniqueAuthors, Emotions, EmoteDensity, Repetition, ClipActivity
│       └── FiltreAdaptatif base (Welford algorithm for adaptive thresholds)
├── Brain (mainBrainTwitch.py)
│   ├── Analyzes filter scores with weighted scoring
│   ├── Manages clip decision logic (merge windows, cooldowns)
│   └── Triggers StreamCapture when threshold exceeded
├── Renderer (mainRendererTwitch.py)
│   └── Discord bot — sends clips with keep/highlight/delete buttons
├── StreamCapture (streamCapture.py)
│   └── Uses streamlink + ffmpeg to capture/clip video buffer
└── DecisionLogger (decisions.py)
    └── JSON session logs in decisions/ directory
```

## Key Concepts

**Filter Weights** (defined in `mainBrainTwitch.py`):
- UniqueAuthors: 0.35
- MessageRate: 0.25
- Emotions: 0.25
- EmoteDensity: 0.20
- ClipActivity: 0.15
- Repetition: 0.10

**Clip Decision Flow**: Filters run on each chat message → Brain aggregates scores (weighted) → If score >= 0.45 and volume filters pass → recording starts → after hype ends, clip is cut and sent to Discord.

**Adaptive Filters**: `FiltreAdaptatif` uses Welford's online algorithm to maintain running mean/std statistics. Filters calibrate automatically over first ~50 samples, then detect spikes above `z_score` threshold relative to recent baseline.

## Key Files

- `src/a3/main.py` — Entry point
- `src/a3/config.py` — Environment variable loader (TOKEN_TWITCH, CHANNELS, CLIENT_ID, CLIENT_SECRET)
- `src/a3/Twitch/mainTwitch.py` — Bot initialization and logging setup
- `src/a3/Twitch/Brain/mainBrainTwitch.py` — Core clip decision logic
- `src/a3/Twitch/Watcher/mainWatcherTwitch.py` — Filter orchestration and calibration monitoring
- `src/a3/Twitch/Renderer/mainRendererTwitch.py` — Discord integration with review UI
- `src/a3/Twitch/Brain/streamCapture.py` — Video buffer and clip generation (streamlink + ffmpeg)
- `src/a3/Twitch/Watcher/filtres/watcherFiltreBase.py` — `FiltreAdaptatif` base class with Welford statistics
- `src/a3/Twitch/Brain/decisions.py` — `DecisionLogger` for session JSON logs

## Required External Tools

- `streamlink` — Stream capture
- `ffmpeg` — Video segmenting and clip encoding
- Discord bot token and channel ID (configured in `mainRendererTwitch.py`)

## Configuration

Environment variables (in `.env`):
- `TOKEN_TWITCH` — Twitch OAuth token
- `CHANNELS` — Comma-separated Twitch channel names
- `CHANNEL_ID` — Channel ID for BTTV/FFZ/7TV API
- `CLIENT_ID`, `CLIENT_SECRET` — Twitch API credentials

## Development

```bash
# Lint
ruff check src/

# Type check
mypy src/

# Tests (none yet in tests/ directory)
pytest
```

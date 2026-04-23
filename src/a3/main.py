# src/a3/main.py
# Point d'entrée global du projet A3.

import logging
from pathlib import Path

from a3.Twitch.mainTwitch import TwitchBot

LOG_DIR = Path("logs")


def _setup_logger() -> logging.Logger:
    LOG_DIR.mkdir(exist_ok=True)
    logger = logging.getLogger("A3")
    logger.setLevel(logging.DEBUG)
    from datetime import datetime

    fh = logging.FileHandler(LOG_DIR / f"a3_{datetime.now():%Y-%m-%d_%H-%M-%S}.log", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fh.formatter)
    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger


if __name__ == "__main__":
    logger = _setup_logger()
    bot = TwitchBot(logger)
    bot.run()

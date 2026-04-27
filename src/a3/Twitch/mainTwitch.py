# src/a3/Twitch/mainTwitch.py

import logging
from datetime import datetime
from pathlib import Path

from twitchio.ext import commands

from a3.config import CHANNELS, TOKEN
from a3.Twitch.Brain.decisions import DecisionLogger
from a3.Twitch.Brain.mainBrainTwitch import Brain
from a3.Twitch.Brain.streamCapture import StreamCapture
from a3.Twitch.Renderer.mainRendererTwitch import Renderer
from a3.Twitch.Watcher.mainWatcherTwitch import Watcher

LOG_DIR = Path("logs")

# ── Helpers ──────────────────────────────────────────────────────────


def _creer_dossier(base: Path, nom: str) -> Path:
    d = base / nom
    d.mkdir(exist_ok=True)
    return d

# ------------------------------------------------------------------ #
#  Setup logging                                                     #
# ------------------------------------------------------------------ #


def setup_logging() -> logging.Logger:
    LOG_DIR.mkdir(exist_ok=True)
    horodatage = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_file = LOG_DIR / f"a3_{horodatage}.log"

    logger = logging.getLogger("A3")
    logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)

    logger.addHandler(fh)
    logger.addHandler(ch)

    logger.info(f"📁 Log écrit dans : {log_file.resolve()}")
    return logger


# ------------------------------------------------------------------ #
#  Bot                                                               #
# ------------------------------------------------------------------ #


class TwitchBot(commands.Bot):
    def __init__(self, logger: logging.Logger, single_channel: str | None = None) -> None:
        self._target_channel = single_channel
        channels = [single_channel] if single_channel else CHANNELS
        super().__init__(token=TOKEN, prefix="?", initial_channels=channels)

        self.log = logger
        channel = channels[0]
        self.capture = StreamCapture(channel=channel)
        self.watcher = Watcher()
        self.decision_logger = DecisionLogger()
        self.brain = Brain(logger=logger, decision_logger=self.decision_logger, channel=channel)
        self.renderer = Renderer(decision_logger=self.decision_logger, struct_log=self.brain._struct_log)

    async def event_ready(self) -> None:
        channels_str = ", ".join(self._target_channel or CHANNELS)
        self.log.info(f"👀 BOT ACTIVÉ : Connecté au chat de {channels_str}")
        self.log.info("-" * 50)

        self.decision_logger._start_cleanup()
        await self.capture.demarrer()
        await self.watcher.start(self.brain, self.renderer)
        await self.brain.start(self.capture, self.renderer)
        await self.renderer.start()

    async def event_message(self, message) -> None:
        if message.echo:
            return
        await self.watcher.handle(message)

    async def close(self) -> None:
        await self.capture.arreter()
        await self.brain.stop()
        await self.renderer.stop()
        await super().close()


# ------------------------------------------------------------------ #
#  Entrée                                                            #
# ------------------------------------------------------------------ #

if __name__ == "__main__":
    logger = setup_logging()
    bot = TwitchBot(logger)

    try:
        bot.run()
    except KeyboardInterrupt:
        logger.info("\nArrêt manuel demandé (Ctrl+C). Fermeture en cours...")

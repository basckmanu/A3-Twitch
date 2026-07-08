# src/a3/Twitch/mainTwitch.py

import asyncio
import logging
from datetime import datetime
from pathlib import Path

from twitchio.ext import commands

from a3.config import CHANNELS, TOKEN
from a3.Twitch.Brain.decisions import DecisionLogger
from a3.Twitch.Brain.mainBrainTwitch import Brain
from a3.Twitch.Brain.streamCapture import StreamCapture
from a3.Twitch.Brain.structuredLogger import StructuredLogger
from a3.Twitch.Renderer.mainRendererTwitch import Renderer
from a3.Twitch.Watcher.mainWatcherTwitch import Watcher

LOG_DIR = Path("logs")


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


class TwitchBot(commands.Bot):
    def __init__(self, logger: logging.Logger, single_channel: str | None = None) -> None:
        self._target_channel = single_channel
        channels = [single_channel] if single_channel else CHANNELS
        super().__init__(token=TOKEN, prefix="?", initial_channels=channels)

        self.log = logger

        # Un DecisionLogger par channel — évite les collisions de clip_num entre streams
        # simultanés (chaque channel numérote ses clips indépendamment à partir de 1)
        self._decision_loggers: dict[str, DecisionLogger] = {ch: DecisionLogger(channel=ch) for ch in channels}

        # Une seule instance de StructuredLogger partagée par tous les Brain et le Renderer
        struct_log_channel = channels[0] if len(channels) == 1 else "multi"
        struct_log = StructuredLogger(channel=struct_log_channel or "unknown")
        self._struct_log = struct_log

        # Un StreamCapture et un Brain par channel surveillé — tous partagent le même StructuredLogger
        self._captures: dict[str, StreamCapture] = {ch: StreamCapture(channel=ch) for ch in channels}
        self._brains: dict[str, Brain] = {
            ch: Brain(logger=logger, decision_logger=self._decision_loggers[ch], channel=ch, structured_logger=struct_log)
            for ch in channels
        }

        self.renderer = Renderer(
            channel=channels[0] if channels else "unknown",
            decision_loggers=self._decision_loggers,
            struct_log=struct_log,
        )
        self.watcher = Watcher(struct_log=struct_log)

    async def event_ready(self) -> None:
        channels_str = ", ".join([self._target_channel] if self._target_channel else CHANNELS)
        self.log.info(f"👀 BOT ACTIVÉ : {len(self._captures)} channel(s) → {channels_str}")
        self.log.info("-" * 50)

        for decision_logger in self._decision_loggers.values():
            decision_logger._start_cleanup()

        for ch, capture in self._captures.items():
            await capture.demarrer()

        await self.watcher.start(self._brains, self.renderer)

        for ch, brain in self._brains.items():
            await brain.start(self._captures[ch], self.renderer)

        await self.renderer.start()

    async def event_message(self, message) -> None:
        if message.echo:
            return
        await self.watcher.handle(message)

    async def close(self) -> None:
        await self.watcher.arreter()
        # Arrêt en parallèle — sinon la durée totale est la somme de chaque channel
        # (ex : 3 streams en plein milieu d'une génération de clip = 3x l'attente)
        # au lieu du max.
        await asyncio.gather(*(capture.arreter() for capture in self._captures.values()), return_exceptions=True)
        await asyncio.gather(*(brain.stop() for brain in self._brains.values()), return_exceptions=True)
        await self.renderer.stop()

        # Sans ce close(), le SESSION_STOP loggé par brain.stop() ci-dessus (et tout
        # event encore en file) reste dans la queue en mémoire : le worker thread du
        # DatabaseHandler est daemon, donc tué instantanément à la sortie du process
        # sans avoir eu la chance de l'écrire. Résultat observé en base : la quasi-
        # totalité des sessions restent 'interrupted' (jamais 'ended'), avec
        # score_avg/clips_detected/duration_seconds jamais renseignés.
        try:
            self._struct_log.close()
        except Exception as e:
            self.log.warning(f"⚠️ Fermeture du StructuredLogger échouée : {e}")

        await super().close()


if __name__ == "__main__":
    logger = setup_logging()
    bot = TwitchBot(logger)

    try:
        bot.run()
    except KeyboardInterrupt:
        logger.info("\nArrêt manuel demandé (Ctrl+C). Fermeture en cours...")

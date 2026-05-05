# src/a3/Twitch/Watcher/mainWatcherTwitch.py

import asyncio
import inspect
import logging
from datetime import datetime

from a3.config import CHANNEL_ID, CLIENT_ID, CLIENT_SECRET, TOKEN
from a3.Twitch.Watcher.filtres.watcherFiltreBase import FiltreAdaptatif
from a3.Twitch.Watcher.filtres.watcherFiltreClipActivity import FiltreClipActivity
from a3.Twitch.Watcher.filtres.watcherFiltreEmoteDensity import FiltreEmoteDensity
from a3.Twitch.Watcher.filtres.watcherFiltreEmotions import FiltreEmotions
from a3.Twitch.Watcher.filtres.watcherFiltreMessageRate import FiltreMessageRate
from a3.Twitch.Watcher.filtres.Watcherfiltrerepetition import FiltreRepetition
from a3.Twitch.Watcher.filtres.watcherFiltreUniqueAuthors import FiltreUniqueAuthors

log = logging.getLogger("A3")


class Watcher:
    def __init__(self) -> None:
        self._brains: dict[str, object] = {}
        self.renderer = None
        # Filtres partagés (globaux, tous channels confondus)
        self.filtres: list = []
        self._calibres: set[str] = set()
        self._tous_calibres: bool = False
        self._ts_debut: float | None = None
        self._monitor_task: asyncio.Task | None = None

    async def start(self, brains: dict, renderer) -> None:
        import time

        self._brains = brains
        self.renderer = renderer
        self._ts_debut = time.time()

        if not CHANNEL_ID or not CLIENT_ID or not CLIENT_SECRET:
            raise EnvironmentError("CHANNEL_ID, CLIENT_ID, CLIENT_SECRET doivent être définis")

        # EmoteDensity et ClipActivity chargent les emotes/clips pour TOUS les channels
        filtre_emote = FiltreEmoteDensity(
            channel_id=CHANNEL_ID,
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            token=TOKEN or "",
        )
        await filtre_emote.initialiser()

        # ClipActivity surveille tous les channels (prend le premier ID comme référence)
        filtre_clips = FiltreClipActivity(
            channel_id=CHANNEL_ID[0] if isinstance(CHANNEL_ID, list) else CHANNEL_ID,
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
        )
        await filtre_clips.initialiser()

        self.filtres = [
            FiltreMessageRate(),
            FiltreUniqueAuthors(),
            FiltreEmotions(),
            filtre_emote,
            FiltreRepetition(),
            filtre_clips,
        ]

        self._filtres_adaptatifs = {f.__class__.__name__: f for f in self.filtres if isinstance(f, FiltreAdaptatif)}

        nb = len(self._filtres_adaptatifs)
        log.info(f"[Watcher] 🔄 Calibration en cours — {nb} filtres adaptatifs à calibrer...")
        log.info(f"[Watcher] Filtres : {', '.join(self._filtres_adaptatifs.keys())}")

        self._monitor_task = asyncio.create_task(self._surveiller_calibration())

    async def handle(self, message) -> None:
        # Routing : envoie le message au Brain du bon channel
        channel_name = message.channel.name if message.channel else None
        brain = self._brains.get(channel_name) if channel_name else None
        if brain is None:
            # fallback : premier brain disponible
            brain = next(iter(self._brains.values()), None)
        if brain is None:
            return
        données = await self._collecter(message)
        await brain.analyze(données)

    async def _collecter(self, message) -> dict:
        résultats = []
        for filtre in self.filtres:
            résultat = filtre.analyser(message)
            if inspect.isawaitable(résultat):
                résultat = await résultat
            résultats.append(résultat)

        détails = {}
        for filtre, résultat in zip(self.filtres, résultats):
            score = float(résultat) if isinstance(résultat, (int, float)) else (1.0 if résultat else 0.0)
            détails[filtre.__class__.__name__] = {
                "score_pondéré": score,
                "passé": score > 0.0,
            }
        mot_repetition = None
        for filtre in self.filtres:
            if isinstance(filtre, FiltreRepetition) and hasattr(filtre, "_dernier_mot_dominant"):
                mot_repetition = filtre._dernier_mot_dominant or None
                break

        return {
            "message": message,
            "timestamp": datetime.now(),
            "détails": détails,
            "mot_repetition": mot_repetition,
        }

    # ── Monitoring calibration ─────────────────────────────────────

    async def _surveiller_calibration(self) -> None:
        import time

        while not self._tous_calibres:
            await asyncio.sleep(10)

            for nom, filtre in self._filtres_adaptatifs.items():
                if nom in self._calibres:
                    continue

                s = filtre.stats()
                samples = s["samples"]
                min_s = filtre.min_samples
                progress = min(samples / min_s * 100, 100)

                if samples >= min_s:
                    self._calibres.add(nom)
                    elapsed = int(time.time() - (self._ts_debut or time.time()))
                    log.info(f"[Watcher] ✅ {nom} calibré ({samples} samples | mean: {s['mean']:.2f} | std: {s['std']:.2f}) — {elapsed}s après démarrage")
                else:
                    log.debug(f"[Watcher] 🔄 {nom} calibration {progress:.0f}% ({samples}/{min_s} samples)")

            # Vérifier si tous calibrés
            if len(self._calibres) == len(self._filtres_adaptatifs):
                self._tous_calibres = True
                elapsed = int(time.time() - (self._ts_debut or time.time()))
                log.info("")
                log.info(f"[Watcher] {'=' * 45}")
                log.info(f"[Watcher] ✅ TOUS LES FILTRES CALIBRÉS — {elapsed}s après démarrage")
                log.info("[Watcher] 🎬 A3 est opérationnel — détection active")
                log.info(f"[Watcher] {'=' * 45}")
                log.info("")

# src/a3/Twitch/Watcher/mainWatcherTwitch.py

import asyncio
import inspect
import logging
from datetime import datetime
from typing import TYPE_CHECKING

from a3.config import CHANNEL_ID, CLIENT_ID, CLIENT_SECRET, TOKEN
from a3.Twitch.Brain.structuredLogger import EventType, StructuredLogger
from a3.Twitch.Watcher.filtres.watcherFiltreBase import FiltreAdaptatif
from a3.Twitch.Watcher.filtres.watcherFiltreClipActivity import FiltreClipActivity
from a3.Twitch.Watcher.filtres.watcherFiltreEmoteDensity import FiltreEmoteDensity
from a3.Twitch.Watcher.filtres.watcherFiltreEmotions import FiltreEmotions
from a3.Twitch.Watcher.filtres.watcherFiltreMessageRate import FiltreMessageRate
from a3.Twitch.Watcher.filtres.watcherFiltreRepetition import FiltreRepetition
from a3.Twitch.Watcher.filtres.watcherFiltreUniqueAuthors import FiltreUniqueAuthors

if TYPE_CHECKING:
    from a3.Twitch.Brain.mainBrainTwitch import Brain

log = logging.getLogger("A3")


class Watcher:
    def __init__(self, struct_log: StructuredLogger | None = None) -> None:
        self._brains: dict[str, "Brain"] = {}
        self.renderer = None
        self._struct_log = struct_log
        # Filtres indépendants par channel (statistiques Welford isolées)
        self._filtres_par_channel: dict[str, list] = {}
        self._filtres_adaptatifs_par_channel: dict[str, dict[str, FiltreAdaptatif]] = {}
        self._calibres_par_channel: dict[str, set[str]] = {}
        self._tous_calibres: bool = False
        self._ts_debut: float | None = None
        self._monitor_task: asyncio.Task | None = None
        # Références pour cleanup
        self._clip_activity_filtres: list[FiltreClipActivity] = []
        self._emote_density_filtres: list[FiltreEmoteDensity] = []

    async def start(self, brains: dict, renderer) -> None:
        import time

        self._brains = brains
        self.renderer = renderer
        self._ts_debut = time.time()

        if not CHANNEL_ID or not CLIENT_ID or not CLIENT_SECRET:
            raise EnvironmentError("CHANNEL_ID, CLIENT_ID, CLIENT_SECRET doivent être définis")

        channels = list(brains.keys())
        channel_ids = CHANNEL_ID if isinstance(CHANNEL_ID, list) else ([CHANNEL_ID] if CHANNEL_ID else [])

        # Un set de filtres par channel — les stats Welford sont ainsi isolées par channel
        for i, ch in enumerate(channels):
            ch_id = channel_ids[i] if i < len(channel_ids) else (channel_ids[0] if channel_ids else "")

            # EmoteDensity : une instance par channel (emotes spécifiques au channel)
            filtre_emote = FiltreEmoteDensity(
                channel_id=ch_id or channel_ids,
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                token=TOKEN or "",
            )
            await filtre_emote.initialiser()
            self._emote_density_filtres.append(filtre_emote)

            # ClipActivity : une instance par channel avec son ID propre
            filtre_clips = FiltreClipActivity(
                channel_id=ch_id,
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
            )
            await filtre_clips.initialiser()
            self._clip_activity_filtres.append(filtre_clips)

            self._filtres_par_channel[ch] = [
                FiltreMessageRate(),
                FiltreUniqueAuthors(),
                FiltreEmotions(),
                filtre_emote,
                FiltreRepetition(),
                filtre_clips,
            ]

        # Index des filtres adaptatifs par channel pour le monitoring calibration
        for ch, filtres in self._filtres_par_channel.items():
            self._filtres_adaptatifs_par_channel[ch] = {
                f.__class__.__name__: f
                for f in filtres
                if isinstance(f, FiltreAdaptatif)
            }
            self._calibres_par_channel[ch] = set()

        nb_total = sum(len(d) for d in self._filtres_adaptatifs_par_channel.values())
        log.info(f"[Watcher] 🔄 Calibration en cours — {nb_total} filtres adaptatifs ({len(channels)} channel(s))")

        self._monitor_task = asyncio.create_task(self._surveiller_calibration())

    async def handle(self, message) -> None:
        channel_name = message.channel.name if message.channel else None
        brain = self._brains.get(channel_name) if channel_name else None
        if brain is None:
            brain = next(iter(self._brains.values()), None)
        if brain is None:
            return

        filtres = self._filtres_par_channel.get(channel_name or "")
        if filtres is None:
            filtres = next(iter(self._filtres_par_channel.values()), [])

        données = await self._collecter(message, channel_name, filtres)
        await brain.analyze(données)

    async def _collecter(self, message, channel_name: str | None = None, filtres: list | None = None) -> dict:
        if filtres is None:
            filtres = []

        résultats = []
        for filtre in filtres:
            résultat = filtre.analyser(message)
            if inspect.isawaitable(résultat):
                résultat = await résultat
            résultats.append(résultat)

        détails = {}
        for filtre, résultat in zip(filtres, résultats):
            score = float(résultat) if isinstance(résultat, (int, float)) else (1.0 if résultat else 0.0)
            détails[filtre.__class__.__name__] = {
                "score_pondéré": score,
                "passé": score > 0.0,
            }

        mot_repetition_hash = None
        for filtre in filtres:
            if isinstance(filtre, FiltreRepetition) and hasattr(filtre, "_dernier_mot_dominant_hash"):
                mot_repetition_hash = filtre._dernier_mot_dominant_hash or None
                break

        return {
            "message": message,
            "timestamp": datetime.now(),
            "détails": détails,
            "mot_repetition": mot_repetition_hash,
            "channel": channel_name,
        }

    # ── Monitoring calibration ─────────────────────────────────────

    async def _surveiller_calibration(self) -> None:
        import time

        while not self._tous_calibres:
            await asyncio.sleep(10)

            for ch, filtres_adaptatifs in self._filtres_adaptatifs_par_channel.items():
                for nom, filtre in filtres_adaptatifs.items():
                    if nom in self._calibres_par_channel[ch]:
                        continue

                    s = filtre.stats()
                    samples = s["samples"]
                    min_s = filtre.min_samples

                    if samples >= min_s:
                        self._calibres_par_channel[ch].add(nom)
                        elapsed = int(time.time() - (self._ts_debut or time.time()))
                        log.info(
                            f"[Watcher] ✅ [{ch}] {nom} calibré "
                            f"({samples} samples | mean: {s['mean']:.2f} | std: {s['std']:.2f}) "
                            f"— {elapsed}s après démarrage"
                        )
                        if self._struct_log is not None:
                            self._struct_log.log_event(EventType.FILTER_CALIBRATED, {
                                "filtre": nom,
                                "samples": samples,
                                "mean": round(s["mean"], 4),
                                "std": round(s["std"], 4),
                                "min_samples": min_s,
                                "z_score": filtre.z_score,
                                "mean_fond": round(s.get("mean_fond", 0.0), 4),
                                "std_fond": 0.0,
                            }, channel=ch)
                    else:
                        progress = min(samples / min_s * 100, 100)
                        log.debug(f"[Watcher] 🔄 [{ch}] {nom} calibration {progress:.0f}% ({samples}/{min_s})")

            # Tous calibrés quand chaque channel a calibré tous ses filtres adaptatifs
            if all(
                len(self._calibres_par_channel[ch]) >= len(self._filtres_adaptatifs_par_channel[ch])
                for ch in self._filtres_adaptatifs_par_channel
            ):
                self._tous_calibres = True
                elapsed = int(time.time() - (self._ts_debut or time.time()))
                log.info("")
                log.info(f"[Watcher] {'=' * 45}")
                log.info(f"[Watcher] ✅ TOUS LES FILTRES CALIBRÉS — {elapsed}s après démarrage")
                log.info("[Watcher] 🎬 A3 est opérationnel — détection active")
                log.info(f"[Watcher] {'=' * 45}")
                log.info("")

    async def arreter(self) -> None:
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass

        for filtre in self._clip_activity_filtres:
            await filtre.arreter()

        for filtre in self._emote_density_filtres:
            if filtre._refresh_task:
                filtre._refresh_task.cancel()
                try:
                    await filtre._refresh_task
                except asyncio.CancelledError:
                    pass

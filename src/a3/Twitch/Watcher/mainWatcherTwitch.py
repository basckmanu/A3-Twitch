# src/a3/Twitch/Watcher/mainWatcherTwitch.py

import asyncio
import inspect
import logging
from datetime import datetime, timezone
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
from a3.Twitch.Watcher.streamMetadata import StreamMetadataPoller

if TYPE_CHECKING:
    from a3.Twitch.Brain.mainBrainTwitch import Brain

log = logging.getLogger("A3")

FENETRE_CHAT_SEC = 60.0  # durée d'agrégation d'une fenêtre chat_windows


class Watcher:
    def __init__(self, struct_log: StructuredLogger | None = None) -> None:
        self._brains: dict[str, "Brain"] = {}
        self.renderer = None
        self._struct_log = struct_log
        # Filtres indépendants par channel (statistiques Welford isolées)
        self._filtres_par_channel: dict[str, list] = {}
        self._filtres_adaptatifs_par_channel: dict[str, dict[str, FiltreAdaptatif]] = {}
        self._calibres_par_channel: dict[str, set[str]] = {}
        # Dernier score loggé par (channel, filtre) — évite de réécrire en base
        # le même score à chaque message tant qu'il ne change pas (ex: FiltreClipActivity
        # reste à 1.0 pendant toute sa fenêtre de cooldown, un event par message noyait
        # filter_events sous des dizaines de milliers de lignes identiques).
        self._dernier_score_logue: dict[str, dict[str, float]] = {}
        self._tous_calibres: bool = False
        self._ts_debut: float | None = None
        self._monitor_task: asyncio.Task | None = None
        # Fenêtres de chat agrégées (dataset ML) — une par channel
        self._fenetres: dict[str, dict] = {}
        self._fenetre_task: asyncio.Task | None = None
        # Références pour cleanup
        self._clip_activity_filtres: list[FiltreClipActivity] = []
        self._emote_density_filtres: list[FiltreEmoteDensity] = []
        self._stream_metadata_par_channel: dict[str, StreamMetadataPoller] = {}
        self._stream_metadata_pollers: list[StreamMetadataPoller] = []

    async def start(self, brains: dict, renderer) -> None:
        import time

        self._brains = brains
        self.renderer = renderer
        self._ts_debut = time.time()

        if not CHANNEL_ID or not CLIENT_ID or not CLIENT_SECRET:
            raise EnvironmentError("CHANNEL_ID, CLIENT_ID, CLIENT_SECRET doivent être définis")

        channels = list(brains.keys())
        channel_ids = CHANNEL_ID if isinstance(CHANNEL_ID, list) else ([CHANNEL_ID] if CHANNEL_ID else [])

        # CHANNEL_ID doit avoir une entrée par channel surveillé, dans le même ordre que
        # CHANNELS. Sans ce garde-fou, un channel sans ID correspondant retombait
        # silencieusement sur channel_ids[0] : ses filtres EmoteDensity/ClipActivity
        # traquaient alors les emotes/clips d'UN AUTRE streamer sans jamais log d'erreur.
        if len(channel_ids) < len(channels):
            raise EnvironmentError(
                f"CHANNEL_ID n'a que {len(channel_ids)} ID(s) pour {len(channels)} channel(s) dans CHANNELS "
                f"({', '.join(channels)}). Renseigne un CHANNEL_ID par channel, dans le même ordre que CHANNELS."
            )

        # Un set de filtres par channel — les stats Welford sont ainsi isolées par channel
        for i, ch in enumerate(channels):
            ch_id = channel_ids[i]

            # EmoteDensity : une instance par channel (emotes spécifiques au channel)
            filtre_emote = FiltreEmoteDensity(
                channel_id=ch_id,
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

            # Métadonnées stream (viewer_count/game/language) : une instance par channel
            poller_metadata = StreamMetadataPoller(
                channel_id=ch_id,
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
            )
            await poller_metadata.initialiser()
            self._stream_metadata_par_channel[ch] = poller_metadata
            self._stream_metadata_pollers.append(poller_metadata)

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
        self._fenetre_task = asyncio.create_task(self._boucle_fenetres())

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
        self._accumuler_fenetre(channel_name or "", données)
        resultat = await brain.analyze(données)
        if resultat is not None:
            fen = self._fenetres.get(channel_name or "")
            if fen is not None:
                fen["clip_num_declenche"] = brain.clips_detectes

    async def _collecter(self, message, channel_name: str | None = None, filtres: list | None = None) -> dict:
        if filtres is None:
            filtres = []

        résultats = []
        for filtre in filtres:
            résultat = filtre.analyser(message)
            if inspect.isawaitable(résultat):
                résultat = await résultat
            résultats.append(résultat)

        auteur = message.author.name if message and message.author else ""
        détails = {}
        for filtre, résultat in zip(filtres, résultats):
            score = float(résultat) if isinstance(résultat, (int, float)) else (1.0 if résultat else 0.0)
            détails[filtre.__class__.__name__] = {
                "score_pondéré": score,
                "passé": score > 0.0,
            }
            if score > 0.0 and self._struct_log is not None:
                nom_filtre = filtre.__class__.__name__
                scores_channel = self._dernier_score_logue.setdefault(channel_name or "", {})
                if scores_channel.get(nom_filtre) != score:
                    scores_channel[nom_filtre] = score
                    self._struct_log.log_filter_score(
                        filtre=nom_filtre,
                        score_raw=score,
                        score_pondere=score,
                        auteur=auteur,
                        channel=channel_name,
                    )
            else:
                self._dernier_score_logue.get(channel_name or "", {}).pop(filtre.__class__.__name__, None)

        mot_repetition_hash = None
        for filtre in filtres:
            if isinstance(filtre, FiltreRepetition) and hasattr(filtre, "_dernier_mot_dominant_hash"):
                mot_repetition_hash = filtre._dernier_mot_dominant_hash or None
                break

        metadata = self._stream_metadata_par_channel.get(channel_name or "")

        return {
            "message": message,
            "timestamp": datetime.now(),
            "détails": détails,
            "mot_repetition": mot_repetition_hash,
            "channel": channel_name,
            "viewer_count": metadata.viewer_count if metadata else None,
            "game_category": metadata.game_name if metadata else None,
            "stream_language": metadata.language if metadata else None,
        }

    # ── Fenêtres de chat agrégées (dataset ML) ──────────────────────

    def _nouvelle_fenetre(self) -> dict:
        import time

        return {
            "debut": time.time(),
            "message_count": 0,
            "auteurs_uniques": set(),
            "sum_message_rate": 0.0,
            "sum_emote_density": 0.0,
            "sum_emotion": 0.0,
            "sum_repetition": 0.0,
            "clip_activity_max": 0.0,
            "clip_num_declenche": None,
            "viewer_count": None,
            "game_category": None,
        }

    def _accumuler_fenetre(self, channel_name: str, données: dict) -> None:
        """Alimente la fenêtre en cours pour ce channel avec les scores bruts
        du message (avant lissage par la mémoire de Brain)."""
        détails = données.get("détails", {})
        message = données.get("message")
        auteur = message.author.name if message and message.author else ""

        fen = self._fenetres.setdefault(channel_name, self._nouvelle_fenetre())
        fen["message_count"] += 1
        if auteur:
            fen["auteurs_uniques"].add(auteur)

        sommes = {
            "FiltreMessageRate": "sum_message_rate",
            "FiltreEmoteDensity": "sum_emote_density",
            "FiltreEmotions": "sum_emotion",
            "FiltreRepetition": "sum_repetition",
        }
        for nom_filtre, cle_sum in sommes.items():
            score = détails.get(nom_filtre, {}).get("score_pondéré", 0.0)
            fen[cle_sum] += score

        score_clip_activity = détails.get("FiltreClipActivity", {}).get("score_pondéré", 0.0)
        fen["clip_activity_max"] = max(fen["clip_activity_max"], score_clip_activity)

        if données.get("viewer_count") is not None:
            fen["viewer_count"] = données["viewer_count"]
        if données.get("game_category") is not None:
            fen["game_category"] = données["game_category"]

    async def _boucle_fenetres(self) -> None:
        while True:
            await asyncio.sleep(FENETRE_CHAT_SEC)
            await self._flush_fenetres()

    async def _flush_fenetres(self) -> None:
        import time

        for channel_name, fen in list(self._fenetres.items()):
            if fen["message_count"] == 0 and fen["clip_num_declenche"] is None:
                fen["debut"] = time.time()
                continue

            n = fen["message_count"] or 1
            if self._struct_log is not None:
                self._struct_log.log_chat_window(
                    window_start=datetime.fromtimestamp(fen["debut"], tz=timezone.utc),
                    window_end=datetime.now(timezone.utc),
                    message_count=fen["message_count"],
                    unique_authors_count=len(fen["auteurs_uniques"]),
                    message_rate_avg=fen["sum_message_rate"] / n,
                    emote_density_avg=fen["sum_emote_density"] / n,
                    emotion_score_avg=fen["sum_emotion"] / n,
                    repetition_score_avg=fen["sum_repetition"] / n,
                    clip_activity_score=fen["clip_activity_max"],
                    clip_num=fen["clip_num_declenche"],
                    viewer_count=fen["viewer_count"],
                    game_category=fen["game_category"],
                    channel=channel_name,
                )
            self._fenetres[channel_name] = self._nouvelle_fenetre()

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

        if self._fenetre_task:
            self._fenetre_task.cancel()
            try:
                await self._fenetre_task
            except asyncio.CancelledError:
                pass
            await self._flush_fenetres()

        for filtre in self._clip_activity_filtres:
            await filtre.arreter()

        for poller in self._stream_metadata_pollers:
            await poller.arreter()

        for filtre_emote in self._emote_density_filtres:
            if filtre_emote._refresh_task:
                filtre_emote._refresh_task.cancel()
                try:
                    await filtre_emote._refresh_task
                except asyncio.CancelledError:
                    pass

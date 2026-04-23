# src/a3/Twitch/tests/test_filtres_live.py

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from twitchio.ext import commands

from a3.config import CHANNEL_ID, CHANNELS, CLIENT_ID, CLIENT_SECRET, TOKEN
from a3.Twitch.Watcher.filtres.watcherFiltreBase import FiltreAdaptatif
from a3.Twitch.Watcher.filtres.watcherFiltreEmoteDensity import FiltreEmoteDensity
from a3.Twitch.Watcher.filtres.watcherFiltreEmotions import FiltreEmotions
from a3.Twitch.Watcher.filtres.watcherFiltreMessageRate import FiltreMessageRate
from a3.Twitch.Watcher.filtres.Watcherfiltrerepetition import FiltreRepetition
from a3.Twitch.Watcher.filtres.watcherFiltreUniqueAuthors import FiltreUniqueAuthors

# ------------------------------------------------------------------ #
#  Config                                                              #
# ------------------------------------------------------------------ #

DASHBOARD_INTERVAL = 300
FENETRE_COINCIDENCE = 25.0
LOG_DIR = Path("logs")


# ------------------------------------------------------------------ #
#  Setup logging                                                       #
# ------------------------------------------------------------------ #


def setup_logging() -> logging.Logger:
    LOG_DIR.mkdir(exist_ok=True)
    horodatage = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_file = LOG_DIR / f"test_{horodatage}.log"

    logger = logging.getLogger("test_filtres")
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
#  Stats par filtre                                                    #
# ------------------------------------------------------------------ #


@dataclass
class StatFiltre:
    nom: str
    actif: bool = True
    declenchements: int = 0
    dernier_declenchement: float = 0.0
    scores: list[float] = field(default_factory=list)

    def enregistrer(self, score: float) -> None:
        self.declenchements += 1
        self.dernier_declenchement = time.time()
        self.scores.append(score)

    def derniere_activite(self) -> str:
        if self.dernier_declenchement == 0:
            return "jamais"
        delta = int(time.time() - self.dernier_declenchement)
        if delta < 60:
            return f"il y a {delta}s"
        return f"il y a {delta // 60}m{delta % 60:02d}s"

    def score_moyen(self) -> float:
        return sum(self.scores) / len(self.scores) if self.scores else 0.0


# ------------------------------------------------------------------ #
#  Fenêtre de coïncidence                                             #
# ------------------------------------------------------------------ #


class FenetreCoincidence:
    def __init__(self, duree: float = FENETRE_COINCIDENCE) -> None:
        self.duree = duree
        self._actifs: dict[str, tuple[float, float]] = {}
        self.moments: list[dict] = []

    def enregistrer(self, nom: str, score: float, message) -> dict | None:
        maintenant = time.time()

        self._actifs = {n: (t, s) for n, (t, s) in self._actifs.items() if maintenant - t <= self.duree}
        self._actifs[nom] = (maintenant, score)

        if len(self._actifs) >= 2:
            moment = {
                "timestamp": datetime.now(),
                "message": message.content[:80],
                "auteur": message.author.name,
                "filtres": {n: s for n, (_, s) in self._actifs.items()},
            }
            if not self.moments or (maintenant - self.moments[-1].get("_ts", 0)) > self.duree:
                moment["_ts"] = maintenant
                self.moments.append(moment)
                return moment

        return None

    def nb_moments(self) -> int:
        return len(self.moments)


# ------------------------------------------------------------------ #
#  Adaptateur evaluer/analyser                                        #
# ------------------------------------------------------------------ #


async def appeler_filtre(filtre: FiltreAdaptatif, message) -> float:
    if hasattr(filtre, "evaluer") and callable(filtre.evaluer):
        résultat = await filtre.evaluer(message)
    elif hasattr(filtre, "analyser") and callable(filtre.analyser):
        résultat = filtre.analyser(message)
    else:
        raise AttributeError(f"{filtre.__class__.__name__} n'expose ni evaluer() ni analyser()")

    if isinstance(résultat, bool):
        return 1.0 if résultat else 0.0
    return float(résultat)


# ------------------------------------------------------------------ #
#  Bot de test                                                         #
# ------------------------------------------------------------------ #


class BotTest(commands.Bot):
    def __init__(self, logger: logging.Logger) -> None:
        channels = [CHANNELS] if isinstance(CHANNELS, str) else CHANNELS
        super().__init__(token=TOKEN, prefix="?", initial_channels=channels)

        self.log = logger
        self.total_messages = 0
        self.debut = time.time()

        self.filtres: dict[str, FiltreAdaptatif] = {
            "MessageRate": FiltreMessageRate(
                fenetre_welford=300,
                fenetre_fond=1200,
                ratio_fond_min=1.5,
            ),
            "UniqueAuthors": FiltreUniqueAuthors(
                fenetre_welford=300,
                fenetre_fond=1200,
                ratio_fond_min=1.5,
            ),
            "Emotions": FiltreEmotions(),
            "Repetition": FiltreRepetition(
                fenetre_welford=300,
                fenetre_fond=1200,
                ratio_fond_min=1.5,
            ),
        }

        self.emote_density = FiltreEmoteDensity(
            channel_id=CHANNEL_ID,
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            token=TOKEN,
            fenetre_welford=300,
            fenetre_fond=1200,
            z_score=3.0,
            ratio_fond_min=1.5,
            seuil_absolu=0.15,
        )

        self.stats: dict[str, StatFiltre] = {nom: StatFiltre(nom=nom) for nom in self.filtres}
        self.stats["EmoteDensity"] = StatFiltre(nom="EmoteDensity", actif=False)

        self.coincidence = FenetreCoincidence(duree=FENETRE_COINCIDENCE)

    async def event_ready(self) -> None:
        channels = ", ".join(CHANNELS if isinstance(CHANNELS, list) else [CHANNELS])
        self.log.info(f"🧪 BOT TEST connecté sur : {channels}")
        self.log.info(f"📋 Filtres : {', '.join(self.filtres)} + EmoteDensity (chargement...)")

        try:
            await self.emote_density.initialiser()
            self.filtres["EmoteDensity"] = self.emote_density
            self.stats["EmoteDensity"].actif = True
            self.log.info("[EmoteDensity] ✅ Prêt")
        except Exception as e:
            self.log.warning(f"[EmoteDensity] ❌ Init échouée : {e} — filtre désactivé")

        self.log.info("-" * 50)
        self.log.info("En attente de messages...")
        asyncio.create_task(self._dashboard_periodique())

    async def event_message(self, message) -> None:
        if message.echo:
            return

        self.total_messages += 1
        declenchements: dict[str, float] = {}

        for nom, filtre in self.filtres.items():
            stat = self.stats[nom]
            if not stat.actif:
                continue
            try:
                score = await appeler_filtre(filtre, message)
                if score > 0:
                    stat.enregistrer(score)
                    declenchements[nom] = score
            except Exception as e:
                self.log.error(f"[{nom}] Erreur sur msg de {message.author.name} : {e}")

        if not declenchements:
            return

        total = sum(s.declenchements for s in self.stats.values())

        moment_fort = None
        for nom, score in declenchements.items():
            moment = self.coincidence.enregistrer(nom, score, message)
            if moment:
                moment_fort = moment

        if moment_fort:
            nb_filtres = len(moment_fort["filtres"])
            moment_str = " | ".join(f"{n}: {s:.2f}" for n, s in moment_fort["filtres"].items())
            icone = "🔥" if nb_filtres >= 3 else "⚡"
            label = f"MULTI ({nb_filtres} filtres)" if nb_filtres >= 3 else "DOUBLE"
            self.log.info(f"{icone} {label} #{self.coincidence.nb_moments()} [{moment_str}] @{message.author.name}")
        elif len(declenchements) == 1:
            nom = list(declenchements.keys())[0]
            self.log.debug(f"· {nom} score: {declenchements[nom]:.2f} @{message.author.name}")
        else:
            filtres_str = " | ".join(f"{n}: {s:.2f}" for n, s in declenchements.items())
            self.log.info(f"⚡ MÊME MSG #{total} [{filtres_str}] @{message.author.name}")

    async def _dashboard_periodique(self) -> None:
        while True:
            await asyncio.sleep(DASHBOARD_INTERVAL)
            self._afficher_bilan_intermediaire()

    def _welford_str(self, nom: str) -> str:
        filtre = self.filtres.get(nom)
        if not isinstance(filtre, FiltreAdaptatif):
            return ""
        w = filtre.stats()
        if w["samples"] < filtre.min_samples:
            return f" [calibration: {w['samples']}/{filtre.min_samples}]"
        # Affiche maintenant aussi la baseline de fond
        return f" [mean: {w['mean']:.2f} | std: {w['std']:.2f} | seuil: {w['seuil']:.2f} | fond: {w['mean_fond']:.2f}]"

    def _afficher_bilan_intermediaire(self) -> None:
        duree = int(time.time() - self.debut)
        minutes = duree // 60
        total = sum(s.declenchements for s in self.stats.values())

        self.log.info("")
        self.log.info(f"{'─' * 65}")
        self.log.info(f"📊 BILAN — {minutes} min écoulées")
        self.log.info(f"   Messages traités      : {self.total_messages}")
        self.log.info(f"   Total déclenchements  : {total}")
        self.log.info(f"   Moments multi-filtres : {self.coincidence.nb_moments()} (fenêtre {FENETRE_COINCIDENCE:.0f}s)")
        self.log.info("")

        for nom, stat in self.stats.items():
            if not stat.actif:
                self.log.info(f"   ⚫ {nom:<20} — désactivé")
                continue
            taux = stat.declenchements / max(self.total_messages, 1) * 100
            icone = "🟢" if stat.declenchements > 0 else "🟡"
            self.log.info(f"   {icone} {nom:<20} {stat.declenchements:>4} fois ({taux:.3f}% msgs) score moy: {stat.score_moyen():.2f} — {stat.derniere_activite()}{self._welford_str(nom)}")

        self.log.info(f"{'─' * 65}")
        self.log.info("")

    def afficher_bilan_final(self) -> None:
        duree = int(time.time() - self.debut)
        minutes = duree // 60
        total = sum(s.declenchements for s in self.stats.values())
        heures = minutes // 60
        mins_restantes = minutes % 60
        duree_str = f"{heures}h{mins_restantes:02d}min" if heures > 0 else f"{minutes} min"
        nb_moments = self.coincidence.nb_moments()
        rythme = nb_moments / max(minutes, 1) * 60

        self.log.info("")
        self.log.info(f"{'=' * 65}")
        self.log.info("🏁 BILAN FINAL")
        self.log.info("")
        self.log.info(f"   ⏱️  Durée du live         : {duree_str}")
        self.log.info(f"   💬 Messages traités      : {self.total_messages}")
        self.log.info(f"   🎬 Moments multi-filtres : {nb_moments}")
        self.log.info(f"   📈 Rythme                : {rythme:.1f} moments/heure")
        self.log.info(f"   ⚡ Total déclenchements  : {total} (tous filtres confondus)")
        self.log.info("")
        self.log.info("   DÉTAIL PAR FILTRE :")

        for nom, stat in self.stats.items():
            if not stat.actif:
                self.log.info(f"   ⚫ {nom:<20} — désactivé")
                continue
            taux = stat.declenchements / max(self.total_messages, 1) * 100
            self.log.info(f"   {nom:<20} {stat.declenchements:>4} fois ({taux:.3f}% msgs) score moy: {stat.score_moyen():.2f}{self._welford_str(nom)}")

        moments = self.coincidence.moments
        if moments:
            self.log.info("")
            self.log.info(f"   🎬 MOMENTS MULTI-FILTRES ({nb_moments}) :")
            for i, m in enumerate(moments, 1):
                filtres_str = " | ".join(f"{n}: {s:.2f}" for n, s in m["filtres"].items())
                self.log.info(f'   #{i:02d} {m["timestamp"].strftime("%H:%M:%S")} [{filtres_str}] @{m["auteur"]} : "{m["message"]}"')
        else:
            self.log.info(f"   Aucun moment multi-filtres détecté (fenêtre de coïncidence : {FENETRE_COINCIDENCE:.0f}s)")

        self.log.info(f"{'=' * 65}")


# ------------------------------------------------------------------ #
#  Entrée                                                              #
# ------------------------------------------------------------------ #

if __name__ == "__main__":
    logger = setup_logging()
    bot = BotTest(logger)

    try:
        bot.run()
    except KeyboardInterrupt:
        pass
    finally:
        bot.afficher_bilan_final()

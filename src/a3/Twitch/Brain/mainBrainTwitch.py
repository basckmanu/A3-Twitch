# src/a3/Twitch/Brain/mainBrainTwitch.py
#
# Cerveau du système : agrège les scores des filtres et prend les décisions de clip.
# Gère le déclenchement de l'enregistrement, le découpage et l'envoi au Discord.

import asyncio
import hashlib
import logging
import os
import time
from collections import Counter, deque
from datetime import datetime

from a3.Twitch.Brain.structuredLogger import EventType, StructuredLogger

log = logging.getLogger("A3")

# ------------------------------------------------------------------ #
#  Configuration                                                     #
# ------------------------------------------------------------------ #

POIDS_FILTRES: dict[str, float] = {
    "FiltreMessageRate": 0.30,
    "FiltreUniqueAuthors": 0.35,
    "FiltreEmotions": 0.25,
    "FiltreEmoteDensity": 0.20,
    "FiltreRepetition": 0.10,
    "FiltreClipActivity": 0.15,
}

SEUIL_CLIP: float = 0.45
FILTRES_VOLUME = {"FiltreMessageRate", "FiltreUniqueAuthors"}
DECALAGE_RECORD_AVANT_SEC = 45.0   # combien de secondes avant le trigger on commence à enregistrer
DUREE_ATTENTE_HYPE_SEC = 15.0      # durée supplémentaire attendue après le dernier pic
DUREE_MIN_TIKTOK_SEC: float = 65.0
MERGE_WINDOW_SEC: int = 300

COOLDOWN_MIN_SEC: int = 120
COOLDOWN_MAX_SEC: int = 480


def _calculer_cooldown(score: float, seuil: float) -> int:
    ratio = min((score - seuil) / (1.0 - seuil), 1.0) if score > seuil else 0.0
    return int(COOLDOWN_MIN_SEC + ratio * (COOLDOWN_MAX_SEC - COOLDOWN_MIN_SEC))


# ------------------------------------------------------------------ #
#  Brain                                                             #
# ------------------------------------------------------------------ #


class Brain:
    def __init__(
        self,
        seuil: float = SEUIL_CLIP,
        poids: dict[str, float] | None = None,
        logger: logging.Logger | None = None,
        decision_logger=None,
        channel: str = "unknown",
    ) -> None:
        self.seuil = seuil
        self.poids = poids or POIDS_FILTRES
        self.capture = None
        self.renderer = None
        self.decision_logger = decision_logger
        self.channel = channel
        self._struct_log = StructuredLogger(channel=channel)

        self.clips_detectes: int = 0
        self.clips_rejetes: int = 0
        self.historique: list[dict] = []
        self.debut_live: datetime = datetime.now()
        self._ts_dernier_clip: float = 0.0
        self._score_dernier_clip: float = 0.0
        self._cooldown_actuel: int = COOLDOWN_MIN_SEC

        self._memoire_filtres: dict[str, tuple[float, float]] = {}
        self.fenetre_memoire_sec: float = 45.0

        self.is_recording: bool = False
        self._ts_debut_record: float = 0.0
        self._ts_fin_attendue: float = 0.0
        self._donnees_initiales: dict | None = None
        self._score_max_clip: float = 0.0

        # Deduplication : garde les hashes des moments récents (60s)
        self._moments_recents: deque[tuple[float, str]] = deque()
        self._fenetre_dedup_sec: float = 60.0

    async def start(self, capture=None, renderer=None) -> None:
        self.capture = capture
        self.renderer = renderer
        self.debut_live = datetime.now()
        self._struct_log.log_event(EventType.SESSION_START, {
            "channel": self.channel,
            "seuil": self.seuil,
            "poids": self.poids,
        })
        poids_str = " | ".join(f"{k}: {v}" for k, v in self.poids.items())
        log.info(f"[Brain] 🧠 Démarré — seuil: {self.seuil} | mode: Élastique (TikTok >{DUREE_MIN_TIKTOK_SEC}s) | poids: {poids_str}")
        log.info(f"[Brain] {'✅ StreamCapture actif' if capture else '⚠️  pas de capture vidéo'}")
        log.info(f"[Brain] {'✅ Renderer actif' if renderer else '⚠️  pas de renderer'}")

    async def analyze(self, données: dict) -> dict | None:
        détails = données["détails"]
        message = données["message"]
        maintenant = time.time()

        for filtre, valeurs in détails.items():
            score_brut = valeurs.get("score_pondéré", 0.0)
            if score_brut > 0:
                ts_actuel, score_actuel = self._memoire_filtres.get(filtre, (0.0, 0.0))
                self._memoire_filtres[filtre] = (maintenant, max(score_brut, score_actuel))

        score_final = 0.0
        détails_memoire = {}
        for filtre, poids in self.poids.items():
            ts_trigger, score_filtre = self._memoire_filtres.get(filtre, (0.0, 0.0))
            if maintenant - ts_trigger <= self.fenetre_memoire_sec:
                contribution = poids * score_filtre
                score_final += contribution
                détails_memoire[filtre] = {"score_pondéré": score_filtre}
            else:
                détails_memoire[filtre] = {"score_pondéré": 0.0}

        données["détails"] = détails_memoire
        détails = détails_memoire

        if self.is_recording:
            if score_final >= self.seuil:
                nouveau_ts_fin = maintenant + DUREE_ATTENTE_HYPE_SEC
                if nouveau_ts_fin > self._ts_fin_attendue:
                    self._ts_fin_attendue = nouveau_ts_fin
                if score_final > self._score_max_clip:
                    self._score_max_clip = score_final
            return None

        volume_ok = any(détails.get(f, {}).get("score_pondéré", 0.0) > 0 for f in FILTRES_VOLUME)
        if not volume_ok:
            return None

        # ── Deduplication ──────────────────────────────────────────
        moment_hash = self._hash_moment(message, détails)
        maintenant = time.time()
        if self._est_duplicate(moment_hash, maintenant):
            self._log_rejet(message, score_final, détails, "moment déjà capturé (dedup)")
            return None

        if score_final < self.seuil:
            self.clips_rejetes += 1
            self._log_rejet(message, score_final, détails, "score insuffisant")
            return None

        temps_depuis = maintenant - self._ts_dernier_clip

        if temps_depuis < MERGE_WINDOW_SEC:
            if score_final >= self._score_dernier_clip:
                log.info(f"[Brain] 🔀 MERGE — pic égal ou plus fort ({score_final:.2f} >= {self._score_dernier_clip:.2f}), remplacement du clip précédent")
                self._annuler_dernier_clip()
            else:
                self.clips_rejetes += 1
                self._log_rejet(
                    message,
                    score_final,
                    détails,
                    f"merge window ({int(MERGE_WINDOW_SEC - temps_depuis)}s restants, pic plus faible)",
                )
                return None

        elif temps_depuis < self._cooldown_actuel:
            restant = int(self._cooldown_actuel - temps_depuis)
            self.clips_rejetes += 1
            self._log_rejet(message, score_final, détails, f"cooldown ({restant}s restants / {self._cooldown_actuel}s total)")
            return None

        self.clips_detectes += 1
        self.is_recording = True
        self._score_max_clip = score_final

        auteur = message.author.name if message else ""
        self._struct_log.log_clip_detected(
            clip_num=self.clips_detectes,
            score=score_final,
            détails=détails,
            auteur=auteur,
            message=message.content if message else "",
        )

        self._ts_debut_record = maintenant - DECALAGE_RECORD_AVANT_SEC
        self._ts_fin_attendue = maintenant + DUREE_ATTENTE_HYPE_SEC
        self._donnees_initiales = données.copy()

        log.info(f"\n[Brain] 🔴 REC DÉMARRÉ (Clip #{self.clips_detectes}) — Attente de la fin de la hype...")

        asyncio.create_task(self._processus_fin_clip())

        return données

    def _hash_moment(self, message, détails: dict) -> str:
        """Génère un hash du moment (auteur + filtres actifs) pour la dedup."""
        auteur = message.author.name if message else ""
        filtres_actifs = tuple(sorted(k for k, v in détails.items() if v.get("score_pondéré", 0) > 0))
        contenu = f"{auteur}|{filtres_actifs}".encode()
        return hashlib.md5(contenu).hexdigest()[:16]

    def _est_duplicate(self, moment_hash: str, ts: float) -> bool:
        """Retourne True si un moment avec ce hash existe dans la fenêtre deduplication."""
        # Purge les vieux
        limite = ts - self._fenetre_dedup_sec
        while self._moments_recents and self._moments_recents[0][0] < limite:
            self._moments_recents.popleft()

        # Vérifie si déjà vu
        for _, h in self._moments_recents:
            if h == moment_hash:
                return True

        self._moments_recents.append((ts, moment_hash))
        return False

    def _annuler_dernier_clip(self) -> None:
        if self.historique:
            dernier = self.historique[-1]
            chemin = dernier.get("chemin_clip")
            if chemin:
                try:
                    os.remove(chemin)
                    log.info(f"[Brain] 🗑️ Clip précédent supprimé : {chemin}")
                except Exception as e:
                    log.warning(f"[Brain] ⚠️ Impossible de supprimer le clip précédent : {e}")
            self.historique.pop()
            self.clips_detectes -= 1

    async def _processus_fin_clip(self):
        while time.time() < self._ts_fin_attendue:
            await asyncio.sleep(2)

        self.is_recording = False
        self._ts_dernier_clip = time.time()
        self._score_dernier_clip = self._score_max_clip

        self._cooldown_actuel = _calculer_cooldown(self._score_max_clip, self.seuil)
        log.info(f"[Brain] ⏳ Cooldown adaptatif : {self._cooldown_actuel}s (score: {self._score_max_clip:.2f})")

        self._memoire_filtres.clear()

        ts_fin_reelle = time.time()
        duree_calculee = ts_fin_reelle - self._ts_debut_record

        if duree_calculee < DUREE_MIN_TIKTOK_SEC:
            manque = DUREE_MIN_TIKTOK_SEC - duree_calculee
            self._ts_debut_record -= manque
            duree_calculee = DUREE_MIN_TIKTOK_SEC
            log.info(f"[Brain] ⏱️ Durée ajustée à {duree_calculee}s (Minimum TikTok)")
        else:
            log.info(f"[Brain] ⏱️ Le moment a duré longtemps ! Durée finale : {int(duree_calculee)}s")

        donnees = self._donnees_initiales
        donnees["timestamp"] = datetime.now()
        donnees["score_final"] = self._score_max_clip
        self.historique.append(donnees)

        self._log_clip(donnees["message"], self._score_max_clip, donnees["détails"], duree_calculee)

        if self.capture:
            nom = f"clip_{self.clips_detectes:03d}_score{self._score_max_clip:.2f}_{int(time.time())}.mp4"
            chemins = await self.capture.clip_dynamique(
                ts_debut=self._ts_debut_record,
                ts_fin=ts_fin_reelle,
                nom=nom,
            )
            if chemins and chemins.get("hq"):
                log.info(f"[Brain] 🎥 Clip vidéo sauvegardé : {chemins['hq']}")
                donnees["chemin_clip"] = str(chemins["hq"])
                liste_previews = chemins.get("previews", [])
                donnees["chemins_previews"] = [str(p) for p in liste_previews]
                self._struct_log.log_clip_generated(
                    clip_num=self.clips_detectes,
                    score=self._score_max_clip,
                    chemin=str(chemins["hq"]),
                    duree_sec=duree_calculee,
                )
                if liste_previews:
                    log.info(f"[Brain] 🎥 Transmission au Discord de {len(liste_previews)} aperçus découpés.")
            else:
                self._struct_log.log_clip_generated(
                    clip_num=self.clips_detectes,
                    score=self._score_max_clip,
                    chemin=None,
                    duree_sec=duree_calculee,
                )
                log.warning("[Brain] ⚠️  Clip vidéo échoué — buffer insuffisant ?")

        # Log du clip dans decisions/
        if self.decision_logger:
            self.decision_logger.log_clip(
                clip_num=self.clips_detectes,
                score=self._score_max_clip,
                filtres=donnees["détails"],
                chemin=donnees.get("chemin_clip"),
                mot_repetition=donnees.get("mot_repetition"),
            )

        if self.renderer:
            asyncio.create_task(self.renderer.output(donnees))

    def _log_clip(self, message, score: float, détails: dict, duree: float) -> None:
        lignes = [
            f"\n{'=' * 55}",
            f"[Brain] 🎬 CLIP #{self.clips_detectes} VALIDÉ ET DÉCOUPÉ",
            f"  Heure   : {datetime.now().strftime('%H:%M:%S')}",
            f"  Durée   : {int(duree)} secondes",
            f"  Auteur  : {message.author.name}",
            f"  Message : {message.content[:80]}",
            f"  Score   : {score:.2f} / {self.seuil} requis",
            f"  Cooldown suivant : {self._cooldown_actuel}s",
            "  Filtres actifs au déclenchement :",
        ]
        for filtre, valeurs in détails.items():
            if valeurs["score_pondéré"] > 0:
                poids = self.poids.get(filtre, 0.0)
                lignes.append(f"    ✅ {filtre:<25} (+{poids:.2f})")
        lignes.append(f"{'=' * 55}")
        log.info("\n".join(lignes))

    def _log_rejet(self, message, score: float, détails: dict, raison: str) -> None:
        filtres_actifs = [f for f, v in détails.items() if v["score_pondéré"] > 0]
        if not filtres_actifs or score < self.seuil * 0.30:
            return
        ratio = min(score / self.seuil, 1.0)
        barre = "█" * int(ratio * 10) + "░" * (10 - int(ratio * 10))
        log.info(f"[Brain] 🟡 [{barre}] {score:.2f}/{self.seuil} — {raison} — filtres: {', '.join(filtres_actifs)}")

    def _construire_rapport_discord(self) -> str:
        duree = datetime.now() - self.debut_live
        minutes = int(duree.total_seconds() // 60)
        heures = minutes // 60
        mins = minutes % 60
        duree_str = f"{heures}h{mins:02d}min" if heures > 0 else f"{minutes} min"
        total = self.clips_detectes + self.clips_rejetes
        taux = self.clips_detectes / total * 100 if total > 0 else 0

        lignes = [
            "# 🏁 Bilan de session A3",
            f"**Durée** : {duree_str}  |  **Clips** : {self.clips_detectes}  |  **Taux** : {taux:.1f}%",
        ]

        if self.clips_detectes > 0:
            scores = [d["score_final"] for d in self.historique]
            lignes.append(f"**Score moyen** : {sum(scores) / len(scores):.2f}  |  **Score max** : {max(scores):.2f}")

            tranches = {"0.45-0.55": 0, "0.55-0.65": 0, "0.65-0.75": 0, "0.75+": 0}
            for s in scores:
                if s < 0.55:
                    tranches["0.45-0.55"] += 1
                elif s < 0.65:
                    tranches["0.55-0.65"] += 1
                elif s < 0.75:
                    tranches["0.65-0.75"] += 1
                else:
                    tranches["0.75+"] += 1
            dist_str = "  ".join(f"`{k}` ×{v}" for k, v in tranches.items() if v > 0)
            lignes.append(f"**Distribution** : {dist_str}")

            compteur_filtres: Counter = Counter()
            for d in self.historique:
                for filtre, valeurs in d.get("détails", {}).items():
                    if valeurs.get("score_pondéré", 0) > 0:
                        compteur_filtres[filtre] += 1
            top = "  ".join(f"`{f}` ×{c}" for f, c in compteur_filtres.most_common(4))
            lignes.append(f"**Filtres actifs** : {top}")

            heures_clips: Counter = Counter()
            for d in self.historique:
                heures_clips[d["timestamp"].strftime("%Hh")] += 1
            top_h = "  ".join(f"`{h}` ×{c}" for h, c in sorted(heures_clips.items()))
            lignes.append(f"**Par heure** : {top_h}")

            lignes.append("**Clips :**")
            for i, d in enumerate(self.historique, 1):
                ts = d["timestamp"].strftime("%H:%M:%S")
                sc = d["score_final"]
                lignes.append(f"> #{i:02d} `{ts}` — score `{sc:.2f}`")

        return "\n".join(lignes)

    def afficher_bilan_final(self) -> None:
        duree = datetime.now() - self.debut_live
        minutes = int(duree.total_seconds() // 60)
        heures = minutes // 60
        mins = minutes % 60
        duree_str = f"{heures}h{mins:02d}min" if heures > 0 else f"{minutes} min"
        total = self.clips_detectes + self.clips_rejetes
        taux = self.clips_detectes / total * 100 if total > 0 else 0

        log.info(f"\n{'=' * 55}\n🏁 BILAN FINAL A3\n")
        log.info(f"  ⏱️  Durée             : {duree_str}")
        log.info(f"  🎬 Clips générés     : {self.clips_detectes}")
        log.info(f"  ⬜ Clips rejetés     : {self.clips_rejetes}")
        log.info(f"  📈 Taux validation   : {taux:.1f}%")

        if self.clips_detectes > 0:
            scores = [d["score_final"] for d in self.historique]
            log.info(f"  Score moyen         : {sum(scores) / len(scores):.2f}")
            log.info(f"  Score max           : {max(scores):.2f}")
            log.info("\n  🎬 CLIPS DÉTECTÉS :")
            for i, d in enumerate(self.historique, 1):
                ts = d["timestamp"].strftime("%H:%M:%S")
                sc = d["score_final"]
                log.info(f"  #{i:02d} {ts} score:{sc:.2f} → {d.get('chemin_clip', 'erreur')}")
        log.info(f"{'=' * 55}")

    async def stop(self) -> None:
        self.afficher_bilan_final()

        if self.renderer and hasattr(self.renderer, "_channel") and self.renderer._channel:
            try:
                rapport = self._construire_rapport_discord()
                await self.renderer._channel.send(rapport)
                log.info("[Brain] 📊 Rapport de session envoyé sur Discord")
            except Exception as e:
                log.warning(f"[Brain] ⚠️ Impossible d'envoyer le rapport Discord : {e}")

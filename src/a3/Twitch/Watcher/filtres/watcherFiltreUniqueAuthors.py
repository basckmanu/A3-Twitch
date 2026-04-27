# src/a3/Twitch/Watcher/filtres/watcherFiltreUniqueAuthors.py
#
# Filtre qui détecte les bursts d'auteurs uniques (nouveaux viewers actifs).

import logging
import time
from collections import deque

from a3.Twitch.Watcher.filtres.watcherFiltreBase import FiltreAdaptatif

logger = logging.getLogger("A3")


class FiltreUniqueAuthors(FiltreAdaptatif):
    def __init__(
        self,
        fenetre_courte: int = 10,
        quota_spam: int = 3,
        fenetre_welford: int = 300,
        fenetre_fond: int | None = None,
        min_samples: int = 50,
        z_score: float = 1.8,
        ratio_fond_min: float = 1.3,
        duree_min_pic: float = 1.5,
        cooldown: float = 45.0,
    ) -> None:
        super().__init__(
            fenetre_welford=fenetre_welford,
            fenetre_fond=fenetre_fond,
            min_samples=min_samples,
            z_score=z_score,
            ratio_fond_min=ratio_fond_min,
            duree_min_pic=duree_min_pic,
            cooldown=cooldown,
        )
        self.fenetre_courte = fenetre_courte
        self.quota_spam = quota_spam

        self._fenetre_deque: deque[tuple[float, str]] = deque()
        self._fenetre_longue_deque: deque[tuple[float, str]] = deque()
        self._freq_courte: dict[str, int] = {}
        self._auteurs_longs: dict[str, int] = {}
        self._lurkers_count: int = 0

    def _nettoyer_fenetre_courte(self, maintenant: float) -> None:
        while self._fenetre_deque and maintenant - self._fenetre_deque[0][0] > self.fenetre_courte:
            _, auteur = self._fenetre_deque.popleft()
            self._freq_courte[auteur] -= 1
            if self._freq_courte[auteur] == 0:
                del self._freq_courte[auteur]
                if self._auteurs_longs.get(auteur, 0) == 0:
                    self._lurkers_count -= 1

    def _nettoyer_fenetre_longue(self, maintenant: float) -> None:
        while self._fenetre_longue_deque and maintenant - self._fenetre_longue_deque[0][0] > self.fenetre_welford:
            _, auteur = self._fenetre_longue_deque.popleft()
            self._auteurs_longs[auteur] -= 1
            if self._auteurs_longs[auteur] == 0:
                del self._auteurs_longs[auteur]
                if self._freq_courte.get(auteur, 0) == 0:
                    self._lurkers_count -= 1

    def _calculer_signal(self, message, maintenant: float) -> float:
        auteur = message.author.name

        self._nettoyer_fenetre_courte(maintenant)
        self._nettoyer_fenetre_longue(maintenant)

        est_lurker = auteur not in self._auteurs_longs

        self._fenetre_longue_deque.append((maintenant, auteur))
        self._auteurs_longs[auteur] = self._auteurs_longs.get(auteur, 0) + 1

        if self._freq_courte.get(auteur, 0) >= self.quota_spam:
            return float(len(self._freq_courte) + self._lurkers_count)

        self._fenetre_deque.append((maintenant, auteur))
        self._freq_courte[auteur] = self._freq_courte.get(auteur, 0) + 1

        if est_lurker and self._freq_courte[auteur] == 1:
            self._lurkers_count += 1

        return float(len(self._freq_courte) + self._lurkers_count)

    def analyser(self, message) -> float:
        maintenant = time.time()
        signal = self._calculer_signal(message, maintenant)
        self._enregistrer_signal(maintenant, signal)

        score = self._evaluer_signal(signal, maintenant)
        if score > 0.0:
            s = self.stats()
            logger.warning(f"[UniqueAuthors] 🔥 BURST — signal: {signal:.0f} (auteurs: {len(self._freq_courte)} / lurkers: {self._lurkers_count}) / seuil: {s['seuil']:.1f} / score: {score:.3f}")
        return score

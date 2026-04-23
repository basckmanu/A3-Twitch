# src/a3/Twitch/Watcher/filtres/watcherFiltreMessageRate.py

import time
from collections import deque

from a3.Twitch.Watcher.filtres.watcherFiltreBase import FiltreAdaptatif


class FiltreMessageRate(FiltreAdaptatif):
    def __init__(
        self,
        fenetre_courte: int = 10,
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
        self._timestamps: deque[float] = deque()

    def _calculer_signal(self, message) -> float:
        maintenant = time.time()
        self._timestamps.append(maintenant)
        while self._timestamps and maintenant - self._timestamps[0] > self.fenetre_courte:
            self._timestamps.popleft()
        return float(len(self._timestamps))

    def analyser(self, message) -> float:
        maintenant = time.time()
        signal = self._calculer_signal(message)
        self._enregistrer_signal(maintenant, signal)

        score = self._evaluer_signal(signal, maintenant)
        if score > 0.0:
            s = self.stats()
            print(f"[MessageRate] 🔥 PIC — vélocité: {signal:.0f} msgs/{self.fenetre_courte}s / mean: {s['mean']:.1f} / seuil: {s['seuil']:.1f} / score: {score:.3f}")
        return score

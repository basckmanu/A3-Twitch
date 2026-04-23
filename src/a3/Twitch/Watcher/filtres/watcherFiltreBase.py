# src/a3/Twitch/Watcher/filtres/watcherFiltreBase.py

from collections import deque


class _Welford:
    """Calcul en ligne de la moyenne et variance (algorithme de Welford)."""

    __slots__ = ("_n", "_mean", "_M2", "_fenetre", "_buffer")

    def __init__(self, fenetre: int) -> None:
        self._n: int = 0
        self._mean: float = 0.0
        self._M2: float = 0.0
        self._fenetre: int = fenetre
        self._buffer: deque[tuple[float, float]] = deque()

    def ajouter(self, ts: float, valeur: float) -> None:
        while self._buffer and ts - self._buffer[0][0] > self._fenetre:
            _, old = self._buffer.popleft()
            self._n -= 1
            delta = old - self._mean
            self._mean -= delta / max(self._n, 1)
            self._M2 -= delta * (old - self._mean)
            self._M2 = max(self._M2, 0.0)

        self._buffer.append((ts, valeur))
        self._n += 1
        delta = valeur - self._mean
        self._mean += delta / self._n
        self._M2 += delta * (valeur - self._mean)

    @property
    def mean(self) -> float:
        return self._mean

    @property
    def std(self) -> float:
        if self._n < 2:
            return 0.0
        return (self._M2 / (self._n - 1)) ** 0.5

    @property
    def n(self) -> int:
        return self._n


class FiltreAdaptatif:
    def __init__(
        self,
        fenetre_welford: int = 300,
        fenetre_fond: int | None = None,
        min_samples: int = 50,
        z_score: float = 1.8,
        ratio_fond_min: float = 1.3,
        duree_min_pic: float = 1.5,
        cooldown: float = 45.0,
    ) -> None:
        self.fenetre_welford = fenetre_welford
        self.fenetre_fond = fenetre_fond if fenetre_fond is not None else fenetre_welford * 4
        self.min_samples = min_samples
        self.z_score = z_score
        self.ratio_fond_min = ratio_fond_min
        self.duree_min_pic = duree_min_pic
        self.cooldown = cooldown

        self._welford_court = _Welford(fenetre_welford)
        self._welford_long = _Welford(self.fenetre_fond)

        self._debut_pic: float | None = None
        self._dernier_declenchement: float = 0.0

    def _enregistrer_signal(self, ts: float, valeur: float) -> None:
        self._welford_court.ajouter(ts, valeur)
        self._welford_long.ajouter(ts, valeur)

    def _evaluer_signal(self, signal: float, ts: float) -> float:
        """
        Retourne un score gradué entre 0.0 et 1.0 selon l'intensité du signal.

        - 0.0  : signal sous le seuil ou cooldown actif
        - 0.0-1.0 : intensité proportionnelle au z-score au-dessus du seuil
        - 1.0  : signal au double du seuil z ou au-delà
        """
        w = self._welford_court
        wl = self._welford_long

        if w.n < self.min_samples:
            return 0.0

        std = max(w.std, 0.001)
        mean_fond = wl.mean if wl.mean > 0 else w.mean

        z_actuel = (signal - w.mean) / std
        spike_local = z_actuel >= self.z_score
        spike_absolu = signal >= self.ratio_fond_min * mean_fond if mean_fond > 0 else True

        if spike_local and spike_absolu:
            if self._debut_pic is None:
                self._debut_pic = ts
            elif ts - self._debut_pic >= self.duree_min_pic:
                if ts - self._dernier_declenchement >= self.cooldown:
                    self._dernier_declenchement = ts
                    self._debut_pic = None
                    # Score gradué : 0.0 au seuil, 1.0 au double du seuil
                    intensite = (z_actuel - self.z_score) / self.z_score
                    return round(min(max(intensite, 0.0), 1.0), 3)
        else:
            self._debut_pic = None

        return 0.0

    def stats(self) -> dict:
        w = self._welford_court
        wl = self._welford_long
        return {
            "samples": w.n,
            "mean": w.mean,
            "std": w.std,
            "seuil": w.mean + self.z_score * w.std,
            "mean_fond": wl.mean,
            "samples_fond": wl.n,
        }

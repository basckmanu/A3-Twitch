# src/a3/Twitch/Watcher/filtres/watcherFiltreEmotions.py
#
# Filtre qui détecte les pics d'émotions (rire, rage, hype, etc.) via regex.

import logging
import re
import time

from a3.Twitch.Watcher.filtres.watcherFiltreBase import FiltreAdaptatif

logger = logging.getLogger("A3")

PATTERNS_EMOTIONS: dict[str, list[re.Pattern]] = {
    "drole": [
        re.compile(r"\blo+l+\b", re.IGNORECASE),
        re.compile(r"\bmd+r+\b", re.IGNORECASE),
        re.compile(r"\bx+d+\b", re.IGNORECASE),
        re.compile(r"\ba*ha+h+a*\b", re.IGNORECASE),
        re.compile(r"\bptd+r+\b", re.IGNORECASE),
        re.compile(r"\blma+o+\b", re.IGNORECASE),
        re.compile(r"\blmfa+o+\b", re.IGNORECASE),
        re.compile(r"💀|😂|🤣"),
    ],
    "rage": [
        re.compile(r"\bwtf+\b", re.IGNORECASE),
        re.compile(r"\bno+\b", re.IGNORECASE),
        re.compile(r"\bnul+\b", re.IGNORECASE),
        re.compile(r"\bputai+n+\b", re.IGNORECASE),
        re.compile(r"\bmerd+e*\b", re.IGNORECASE),
        re.compile(r"\bhorribl+e*\b", re.IGNORECASE),
        re.compile(r"\bscandale+\b", re.IGNORECASE),
        re.compile(r"😡|🤬"),
    ],
    "hype": [
        re.compile(r"\ble+t+s*\s*go+\b", re.IGNORECASE),
        re.compile(r"\bpo+g+s*\b", re.IGNORECASE),
        re.compile(r"\bgg+\b", re.IGNORECASE),
        re.compile(r"\bgo+\b", re.IGNORECASE),
        re.compile(r"\byes+\b", re.IGNORECASE),
        re.compile(r"\bayay+a+\b", re.IGNORECASE),
        re.compile(r"\bincroyal+e*\b", re.IGNORECASE),
        re.compile(r"\binsane+\b", re.IGNORECASE),
        re.compile(r"\bcraz+y\b", re.IGNORECASE),
        re.compile(r"🔥|🏆|💪|🎉"),
    ],
    "choc": [
        re.compile(r"\bomg+\b", re.IGNORECASE),
        re.compile(r"\bwha+t+\b", re.IGNORECASE),
        re.compile(r"\bno+\s*way+\b", re.IGNORECASE),
        re.compile(r"\bah+\b", re.IGNORECASE),
        re.compile(r"\bsérieu+x\b", re.IGNORECASE),
        re.compile(r"\bvraimen+t\b", re.IGNORECASE),
        re.compile(r"😱|🤯"),
    ],
    "tristesse": [
        re.compile(r"\br+i+p+\b", re.IGNORECASE),
        re.compile(r"\b[fF]+\b"),
        re.compile(r"\boo+f+\b", re.IGNORECASE),
        re.compile(r"\bsni+f+\b", re.IGNORECASE),
        re.compile(r"\bdommage+\b", re.IGNORECASE),
        re.compile(r"\btriste+\b", re.IGNORECASE),
        re.compile(r"😢|😭"),
    ],
    "raid": [
        re.compile(r"\braid+\b", re.IGNORECASE),
        re.compile(r"\bwelco+me+\b", re.IGNORECASE),
        re.compile(r"\bbienven+ue*\b", re.IGNORECASE),
        re.compile(r"\bbonjou+r+\b", re.IGNORECASE),
        re.compile(r"\bsalu+t+\b", re.IGNORECASE),
        re.compile(r"\bhell+o+\b", re.IGNORECASE),
        re.compile(r"\bcouco+u+\b", re.IGNORECASE),
    ],
}


class FiltreEmotions(FiltreAdaptatif):
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
        super().__init__(
            fenetre_welford=fenetre_welford,
            fenetre_fond=fenetre_fond,
            min_samples=min_samples,
            z_score=z_score,
            ratio_fond_min=ratio_fond_min,
            duree_min_pic=duree_min_pic,
            cooldown=cooldown,
        )
        self._dernier_message: str | None = None

    def _calculer_signal(self, message) -> float:
        contenu = message.content
        mots = contenu.split()
        if not mots:
            return 0.0

        classes_actives = 0
        for patterns in PATTERNS_EMOTIONS.values():
            for pattern in patterns:
                if pattern.search(contenu):
                    classes_actives += 1
                    break

        self._dernier_message = contenu
        return classes_actives / len(mots)

    def _classe_dominante(self, contenu: str) -> str:
        scores = {classe: sum(1 for p in patterns if p.search(contenu)) for classe, patterns in PATTERNS_EMOTIONS.items()}
        meilleure = max(scores, key=lambda k: scores[k])
        return meilleure if scores[meilleure] > 0 else "neutre"

    def analyser(self, message) -> float:
        maintenant = time.time()
        signal = self._calculer_signal(message)
        self._enregistrer_signal(maintenant, signal)

        score = self._evaluer_signal(signal, maintenant)
        if score > 0.0:
            s = self.stats()
            classe = self._classe_dominante(self._dernier_message or "")
            logger.warning(f"[Emotions] 🔥 PIC — classe: {classe} / signal: {signal:.2f} / seuil: {s['seuil']:.2f} / score: {score:.3f}")
        return score

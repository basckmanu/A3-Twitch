# src/a3/Twitch/Watcher/filtres/watcherFiltreRepetition.py

import json
import re
import time
from collections import Counter, deque
from pathlib import Path

from a3.Twitch.Watcher.filtres.watcherFiltreBase import FiltreAdaptatif

FICHIER_BLACKLIST = Path("blacklist_mots.json")


def _charger_blacklist() -> set[str]:
    if not FICHIER_BLACKLIST.exists():
        return set()
    try:
        with open(FICHIER_BLACKLIST, encoding="utf-8") as f:
            return {m.lower() for m in json.load(f)}
    except Exception as e:
        import logging
        logging.getLogger("A3").warning(f"[Repetition] ⚠️ Blacklist load failed: {e}")
        return set()


class FiltreRepetition(FiltreAdaptatif):
    def __init__(
        self,
        fenetre_courte: int = 10,
        longueur_min_mot: int = 2,
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
        self.longueur_min_mot = longueur_min_mot
        self._blacklist: set[str] = _charger_blacklist()

        self._fenetre_deque: deque[tuple[float, set[str]]] = deque()
        self._dernier_mot_dominant: str = ""

    MOTS_VIDES = {
        "le",
        "la",
        "les",
        "de",
        "du",
        "des",
        "un",
        "une",
        "et",
        "en",
        "je",
        "tu",
        "il",
        "on",
        "ce",
        "que",
        "qui",
        "pas",
        "sur",
        "au",
        "the",
        "a",
        "is",
        "it",
        "to",
        "of",
        "in",
        "i",
        "you",
        "he",
        "she",
        "we",
        "they",
        "my",
        "your",
        "his",
        "her",
        "its",
    }

    def _extraire_mots(self, contenu: str) -> set[str]:
        texte_propre = re.sub(r"[^\w\s]", "", contenu.lower())
        mots = texte_propre.split()
        # exclut les mots vides ET la blacklist
        return {m for m in mots if len(m) >= self.longueur_min_mot and m not in self.MOTS_VIDES and m not in self._blacklist}

    def _calculer_signal(self, message) -> float:
        maintenant = time.time()
        mots = self._extraire_mots(message.content)

        self._fenetre_deque.append((maintenant, mots))
        while self._fenetre_deque and maintenant - self._fenetre_deque[0][0] > self.fenetre_courte:
            self._fenetre_deque.popleft()

        total = len(self._fenetre_deque)
        if total == 0:
            return 0.0

        compteur: Counter = Counter()
        for _, mots_msg in self._fenetre_deque:
            for mot in mots_msg:
                compteur[mot] += 1

        if not compteur:
            return 0.0

        mot_dominant, count = compteur.most_common(1)[0]
        self._dernier_mot_dominant = mot_dominant
        return count / total

    def analyser(self, message) -> float:
        maintenant = time.time()
        signal = self._calculer_signal(message)
        self._enregistrer_signal(maintenant, signal)

        score = self._evaluer_signal(signal, maintenant)
        if score > 0.0:
            s = self.stats()
            print(f"[Repetition] 🔥 RÉPÉTITION — mot: '{self._dernier_mot_dominant}' / signal: {signal:.2f} / seuil: {s['seuil']:.2f} / score: {score:.3f}")
        return score

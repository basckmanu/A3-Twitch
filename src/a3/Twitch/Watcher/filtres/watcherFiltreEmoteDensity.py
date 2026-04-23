# src/a3/Twitch/Watcher/filtres/watcherFiltreEmoteDensity.py

import asyncio
import hashlib
import json
import logging
import time
from collections import deque
from pathlib import Path

import aiohttp

from a3.Twitch.Watcher.filtres.watcherFiltreBase import FiltreAdaptatif

logger = logging.getLogger("A3")

_CACHE_DIR = Path(__file__).resolve().parents[4] / "cache"
_CACHE_TTL_SEC = 3600  # 1h avant refresh forcé


def _cache_path(channel_ids: list[str]) -> Path:
    key = "_".join(sorted(channel_ids))
    hash_key = hashlib.md5(key.encode()).hexdigest()[:12]
    _CACHE_DIR.mkdir(exist_ok=True)
    return _CACHE_DIR / f"emotes_{hash_key}.json"


class FiltreEmoteDensity(FiltreAdaptatif):
    def __init__(
        self,
        channel_id: str | list[str],
        client_id: str,
        client_secret: str,
        token: str = "",
        fenetre_courte_signal: int = 10,
        fenetre_welford: int = 300,
        fenetre_fond: int | None = None,
        min_samples: int = 50,
        z_score: float = 1.8,
        ratio_fond_min: float = 1.3,
        duree_min_pic: float = 1.5,
        cooldown: float = 45.0,
        seuil_absolu: float = 0.08,
        cache_ttl_sec: int = _CACHE_TTL_SEC,
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
        if isinstance(channel_id, str):
            self.channel_ids = [cid.strip() for cid in channel_id.split(",")]
        else:
            self.channel_ids = [cid.strip() for cid in channel_id]

        self.client_id = client_id
        self.client_secret = client_secret
        self.token = token
        self.emotes: set[str] = set()
        self.fenetre_courte_signal = fenetre_courte_signal
        self.seuil_absolu = seuil_absolu
        self.cache_ttl_sec = cache_ttl_sec
        self._fenetre_deque: deque[tuple[float, float]] = deque()
        self._refresh_task: asyncio.Task | None = None
        self._cache_path = _cache_path(self.channel_ids)

    # ------------------------------------------------------------------ #
    #  Authentification                                                  #
    # ------------------------------------------------------------------ #

    async def _renouveler_token(self, session: aiohttp.ClientSession | None = None) -> None:
        url = "https://id.twitch.tv/oauth2/token"
        payload = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "client_credentials",
        }

        async def _faire_requete(s: aiohttp.ClientSession) -> None:
            async with s.post(url, data=payload) as resp:
                if resp.status != 200:
                    erreur = await resp.text()
                    raise Exception(f"Impossible de générer le token Twitch : {erreur}")
                data = await resp.json()
                self.token = data["access_token"]
                logger.info("[EmoteDensity] 🔑 Token renouvelé")

        if session is not None:
            await _faire_requete(session)
        else:
            async with aiohttp.ClientSession() as s:
                await _faire_requete(s)

    # ------------------------------------------------------------------ #
    #  Résolution slug → ID numérique                                    #
    # ------------------------------------------------------------------ #

    async def _resoudre_channel_id(self, session: aiohttp.ClientSession, slug: str) -> str:
        if slug.isdigit():
            return slug

        url = "https://api.twitch.tv/helix/users"
        headers = {"Client-Id": self.client_id, "Authorization": f"Bearer {self.token}"}

        async with session.get(url, headers=headers, params={"login": slug}) as resp:
            resp.raise_for_status()
            data = await resp.json()
            users = data.get("data", [])
            if not users:
                raise ValueError(f"Channel introuvable : {slug}")
            resolved = users[0]["id"]
            logger.debug(f"[EmoteDensity] 🔍 '{slug}' → ID {resolved}")
            return resolved

    # ------------------------------------------------------------------ #
    #  Cache                                                             #
    # ------------------------------------------------------------------ #

    def _load_cache(self) -> bool:
        if not self._cache_path.exists():
            return False
        try:
            with open(self._cache_path, encoding="utf-8") as f:
                cache = json.load(f)
            age = time.time() - cache.get("_timestamp", 0)
            if age > self.cache_ttl_sec:
                logger.debug(f"[EmoteDensity] Cache expiré ({age:.0f}s > {self.cache_ttl_sec}s)")
                return False
            self.emotes = set(cache.get("emotes", []))
            logger.info(f"[EmoteDensity] 💾 Cache chargé : {len(self.emotes)} emotes (age: {age:.0f}s)")
            return True
        except Exception as e:
            logger.warning(f"[EmoteDensity] ⚠️ Lecture cache échouée: {e}")
            return False

    def _save_cache(self) -> None:
        try:
            cache = {"_timestamp": time.time(), "emotes": list(self.emotes)}
            temp = self._cache_path.with_suffix(".tmp")
            with open(temp, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False, indent=2)
            temp.replace(self._cache_path)
            logger.debug(f"[EmoteDensity] 💾 Cache sauvegardé : {len(self.emotes)} emotes")
        except Exception as e:
            logger.warning(f"[EmoteDensity] ⚠️ Sauvegarde cache échouée: {e}")

    async def _refresh_periodique(self) -> None:
        while True:
            await asyncio.sleep(self.cache_ttl_sec)
            try:
                logger.info("[EmoteDensity] 🔄 Refresh periodic des emotes...")
                async with aiohttp.ClientSession() as session:
                    await self._charger_toutes_emotes(session, dans_cache=True)
            except Exception as e:
                logger.warning(f"[EmoteDensity] ⚠️ Refresh periodic échoué: {e}")

    # ------------------------------------------------------------------ #
    #  Chargement des emotes                                             #
    # ------------------------------------------------------------------ #

    async def initialiser(self) -> None:
        if not self.token:
            await self._renouveler_token()

        # 1. Essayer le cache disque d'abord (rapide)
        if self._load_cache():
            # Lancer refresh en arrière-plan
            self._refresh_task = asyncio.create_task(self._refresh_periodique())
            # Refresh en avant-plan pour mettre à jour si le cache est un peu vieux
            try:
                async with aiohttp.ClientSession() as session:
                    await self._charger_toutes_emotes(session, dans_cache=True)
            except Exception as e:
                logger.warning(f"[EmoteDensity] ⚠️ Refresh après cache échoué: {e}")
            return

        # Pas de cache : chargement complet
        async with aiohttp.ClientSession() as session:
            await self._charger_toutes_emotes(session, dans_cache=True)

        self._refresh_task = asyncio.create_task(self._refresh_periodique())

    async def _charger_toutes_emotes(self, session: aiohttp.ClientSession, *, dans_cache: bool) -> None:
        ids_resolus: list[str] = []
        for cid in self.channel_ids:
            try:
                ids_resolus.append(await self._resoudre_channel_id(session, cid))
            except aiohttp.ClientResponseError as e:
                if e.status == 401:
                    await self._renouveler_token(session)
                    try:
                        ids_resolus.append(await self._resoudre_channel_id(session, cid))
                    except Exception as e2:
                        logger.warning(f"[EmoteDensity] ⚠️ Impossible de résoudre '{cid}' après renouvellement : {e2}")
                else:
                    logger.warning(f"[EmoteDensity] ⚠️ Impossible de résoudre '{cid}' : {e}")
            except Exception as e:
                logger.warning(f"[EmoteDensity] ⚠️ Impossible de résoudre '{cid}' : {e}")

        taches = [
            self._charger_twitch_global(session),
            self._charger_bttv_global(session),
            self._charger_7tv_global(session),
        ]
        for cid in ids_resolus:
            taches += [
                self._charger_bttv_channel(session, cid),
                self._charger_ffz_channel(session, cid),
                self._charger_7tv_channel(session, cid),
            ]

        resultats = await asyncio.gather(*taches, return_exceptions=True)

        erreurs = [r for r in resultats if isinstance(r, Exception)]
        for err in erreurs:
            logger.warning(f"[EmoteDensity] ⚠️ Source indisponible : {err}")

        if not erreurs:
            logger.info("[EmoteDensity] ✅ Toutes les sources chargées")
        else:
            logger.warning(f"[EmoteDensity] ⚠️ {len(erreurs)} source(s) indisponible(s), chargement partiel")

        logger.info(f"[EmoteDensity] 🎉 {len(self.emotes)} emotes chargées au total")

        if dans_cache:
            self._save_cache()

    async def _charger_twitch_global(self, session: aiohttp.ClientSession, retry: bool = True) -> None:
        url = "https://api.twitch.tv/helix/chat/emotes/global"
        headers = {"Client-Id": self.client_id, "Authorization": f"Bearer {self.token}"}

        async with session.get(url, headers=headers) as resp:
            if resp.status == 401 and retry:
                await self._renouveler_token(session)
                return await self._charger_twitch_global(session, retry=False)
            resp.raise_for_status()
            data = await resp.json()
            for emote in data.get("data", []):
                self.emotes.add(emote["name"])

    async def _charger_bttv_global(self, session: aiohttp.ClientSession) -> None:
        async with session.get("https://api.betterttv.net/3/cached/emotes/global") as resp:
            resp.raise_for_status()
            for emote in await resp.json():
                self.emotes.add(emote["code"])

    async def _charger_bttv_channel(self, session: aiohttp.ClientSession, cid: str) -> None:
        async with session.get(f"https://api.betterttv.net/3/cached/users/twitch/{cid}") as resp:
            resp.raise_for_status()
            data = await resp.json()
            for emote in data.get("channelEmotes", []) + data.get("sharedEmotes", []):
                self.emotes.add(emote["code"])

    async def _charger_ffz_channel(self, session: aiohttp.ClientSession, cid: str) -> None:
        async with session.get(f"https://api.frankerfacez.com/v1/room/id/{cid}") as resp:
            resp.raise_for_status()
            data = await resp.json()
            for emote_set in data.get("sets", {}).values():
                for emote in emote_set.get("emoticons", []):
                    self.emotes.add(emote["name"])

    async def _charger_7tv_global(self, session: aiohttp.ClientSession) -> None:
        async with session.get("https://7tv.io/v3/emote-sets/global") as resp:
            resp.raise_for_status()
            for emote in (await resp.json()).get("emotes", []):
                self.emotes.add(emote["name"])

    async def _charger_7tv_channel(self, session: aiohttp.ClientSession, cid: str) -> None:
        async with session.get(f"https://7tv.io/v3/users/twitch/{cid}") as resp:
            resp.raise_for_status()
            data = await resp.json()
            for emote in (data.get("emote_set") or {}).get("emotes", []):
                self.emotes.add(emote["name"])

    # ------------------------------------------------------------------ #
    #  Détection                                                         #
    # ------------------------------------------------------------------ #

    def _calculer_signal(self, message) -> float:
        maintenant = time.time()
        mots = message.content.split()

        if not mots:
            return 0.0

        ratio = sum(1 for m in mots if m in self.emotes) / len(mots)

        self._fenetre_deque.append((maintenant, ratio))
        while self._fenetre_deque and maintenant - self._fenetre_deque[0][0] > self.fenetre_courte_signal:
            self._fenetre_deque.popleft()

        if not self._fenetre_deque:
            return 0.0

        return sum(r for _, r in self._fenetre_deque) / len(self._fenetre_deque)

    def analyser(self, message) -> float:
        maintenant = time.time()
        signal = self._calculer_signal(message)
        self._enregistrer_signal(maintenant, signal)

        if signal < self.seuil_absolu:
            return 0.0

        score = self._evaluer_signal(signal, maintenant)
        if score > 0.0:
            s = self.stats()
            logger.debug(f"[EmoteDensity] 🔥 PIC — densité: {signal:.2f} / mean: {s['mean']:.2f} / seuil: {s['seuil']:.2f} / score: {score:.3f}")
        return score

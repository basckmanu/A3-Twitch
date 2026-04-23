# src/a3/Twitch/Watcher/filtres/watcherFiltreClipActivity.py
#
# Filtre qui détecte quand les viewers clippent activement.
# Un polling en arrière-plan vérifie les nouveaux clips toutes les 30s.
# Si >= SEUIL_CLIPS clips ont été créés dans la dernière minute → score = 1.0

import asyncio
import time
from collections import deque

import aiohttp

# ─────────────────────────────────────────
#  Config
# ─────────────────────────────────────────

INTERVALLE_POLL_SEC = 30
FENETRE_CLIPS_SEC = 90
SEUIL_CLIPS = 1
COOLDOWN_SEC = 60
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5.0


class FiltreClipActivity:
    """
    Filtre indépendant (pas FiltreAdaptatif) car il ne réagit pas
    aux messages chat mais à un polling externe.

    Score retourné :
      - 0.0  : pas assez de clips récents
      - 1.0  : >= SEUIL_CLIPS clips dans FENETRE_CLIPS_SEC secondes
    """

    def __init__(
        self,
        channel_id: str,
        client_id: str,
        client_secret: str,
        seuil_clips: int = SEUIL_CLIPS,
        fenetre_sec: int = FENETRE_CLIPS_SEC,
        cooldown: float = COOLDOWN_SEC,
    ) -> None:
        self.channel_id = channel_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.seuil_clips = seuil_clips
        self.fenetre_sec = fenetre_sec
        self.cooldown = cooldown

        self._clips_recents: deque[float] = deque()  # timestamps des clips détectés
        self._clips_vus: set[str] = set()  # IDs déjà comptabilisés
        self._app_token: str | None = None
        self._score_actuel: float = 0.0
        self._ts_dernier_trigger: float = 0.0
        self._poll_task: asyncio.Task | None = None
        self._session: aiohttp.ClientSession | None = None

    # ── Démarrage / arrêt ──────────────────

    async def initialiser(self) -> None:
        """Démarre le polling en arrière-plan."""
        self._session = aiohttp.ClientSession()
        await self._renouveler_token()
        self._poll_task = asyncio.create_task(self._boucle_poll())
        print(f"[ClipActivity] ✅ Polling démarré — seuil: {self.seuil_clips} clips / {self.fenetre_sec}s")

    async def arreter(self) -> None:
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        if self._session:
            await self._session.close()

    # ── Interface filtre (appelée par Watcher) ─

    def analyser(self, message) -> float:
        """
        Retourne le score actuel.
        Sync — le score est maintenu à jour par le polling en arrière-plan.
        """
        return self._score_actuel

    # ── Polling ────────────────────────────

    async def _boucle_poll(self) -> None:
        while True:
            try:
                await self._verifier_clips()
            except Exception as e:
                print(f"[ClipActivity] ⚠️ Erreur poll: {e}")
            await asyncio.sleep(INTERVALLE_POLL_SEC)

    async def _verifier_clips(self) -> None:
        """Récupère les clips récents et met à jour le score."""
        from datetime import datetime, timezone

        assert self._session is not None, "Session non initialisée"
        maintenant = time.time()
        started_at = datetime.fromtimestamp(maintenant - self.fenetre_sec, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        headers = {
            "Authorization": f"Bearer {self._app_token}",
            "Client-Id": self.client_id,
        }
        params: dict[str, str | int] = {
            "broadcaster_id": self.channel_id,
            "started_at": started_at,
            "first": 20,
        }

        async with self._session.get(
            "https://api.twitch.tv/helix/clips",
            headers=headers,
            params=params,
        ) as resp:
            if resp.status == 401:
                await self._renouveler_token()
                return
            if resp.status != 200:
                return
            data = await resp.json()

        clips = data.get("data", [])
        nouveaux = 0

        for clip in clips:
            clip_id = clip["id"]
            if clip_id not in self._clips_vus:
                self._clips_vus.add(clip_id)
                self._clips_recents.append(maintenant)
                nouveaux += 1

        # Purger les vieux timestamps
        limite = maintenant - self.fenetre_sec
        while self._clips_recents and self._clips_recents[0] < limite:
            self._clips_recents.popleft()

        nb_clips = len(self._clips_recents)

        if nouveaux > 0:
            print(f"[ClipActivity] 📎 {nouveaux} nouveau(x) clip(s) — total fenêtre: {nb_clips}/{self.seuil_clips}")

        # Mise à jour du score
        if nb_clips >= self.seuil_clips:
            temps_depuis = maintenant - self._ts_dernier_trigger
            if temps_depuis >= self.cooldown:
                self._score_actuel = 1.0
                self._ts_dernier_trigger = maintenant
                print(f"[ClipActivity] 🔥 SEUIL ATTEINT — {nb_clips} clips en {self.fenetre_sec}s")
            else:
                self._score_actuel = 0.0  # cooldown actif
        else:
            self._score_actuel = 0.0

    # ── Auth ───────────────────────────────

    async def _renouveler_token(self) -> None:
        assert self._session is not None, "Session non initialisée"
        for tentative in range(MAX_RETRIES):
            try:
                async with self._session.post(
                    "https://id.twitch.tv/oauth2/token",
                    params={
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                        "grant_type": "client_credentials",
                    },
                ) as resp:
                    if resp.status != 200:
                        texte = await resp.text()
                        raise Exception(f"Token refresh échoué ({resp.status}): {texte[:100]}")
                    data = await resp.json()
                    self._app_token = data["access_token"]
                    print("[ClipActivity] 🔑 Token renouvelé")
                    return
            except Exception as e:
                if tentative < MAX_RETRIES - 1:
                    print(f"[ClipActivity] ⚠️ Tentative {tentative + 1}/{MAX_RETRIES} échouée, retry dans {RETRY_BACKOFF_SEC}s: {e}")
                    await asyncio.sleep(RETRY_BACKOFF_SEC * (tentative + 1))
                else:
                    print(f"[ClipActivity] ❌ Toutes les tentatives de renouvellement token épuisées: {e}")
                    self._app_token = None

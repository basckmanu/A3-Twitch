# src/a3/Twitch/Watcher/streamMetadata.py
#
# Polling périodique de l'API Twitch Helix "Get Streams" pour capturer
# viewer_count / game_name / language — jamais suivis jusqu'ici alors que
# clips.viewer_count, clips.game_category, clips.stream_language et
# sessions.avg_viewers existent dans le schéma DB sans jamais être peuplés.

import asyncio
import logging

import aiohttp

logger = logging.getLogger("A3")

INTERVALLE_POLL_SEC = 60
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5.0


class StreamMetadataPoller:
    """Un poller par channel — maintient viewer_count/game_name/language à
    jour en arrière-plan ; lu de façon synchrone par le Watcher à chaque
    message (pas d'appel réseau sur le chemin chaud du chat)."""

    def __init__(self, channel_id: str, client_id: str, client_secret: str) -> None:
        self.channel_id = channel_id
        self.client_id = client_id
        self.client_secret = client_secret

        self.viewer_count: int | None = None
        self.game_name: str | None = None
        self.language: str | None = None

        self._app_token: str | None = None
        self._poll_task: asyncio.Task | None = None
        self._session: aiohttp.ClientSession | None = None

    async def initialiser(self) -> None:
        self._session = aiohttp.ClientSession()
        await self._renouveler_token()
        self._poll_task = asyncio.create_task(self._boucle_poll())
        logger.info(f"[StreamMetadata] ✅ Polling démarré — channel_id={self.channel_id}")

    async def arreter(self) -> None:
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        if self._session:
            await self._session.close()

    async def _boucle_poll(self) -> None:
        while True:
            try:
                await self._verifier_stream()
            except Exception as e:
                logger.warning(f"[StreamMetadata] ⚠️ Erreur poll: {e}")
            await asyncio.sleep(INTERVALLE_POLL_SEC)

    async def _verifier_stream(self) -> None:
        assert self._session is not None, "Session non initialisée"
        headers = {"Authorization": f"Bearer {self._app_token}", "Client-Id": self.client_id}
        params = {"user_id": self.channel_id}

        async with self._session.get(
            "https://api.twitch.tv/helix/streams",
            headers=headers,
            params=params,
        ) as resp:
            if resp.status == 401:
                await self._renouveler_token()
                return
            if resp.status != 200:
                return
            data = await resp.json()

        streams = data.get("data", [])
        if streams:
            self.viewer_count = streams[0].get("viewer_count")
            self.game_name = streams[0].get("game_name") or None
            self.language = streams[0].get("language") or None
        else:
            # Stream hors ligne — pas d'erreur, juste rien à rapporter
            self.viewer_count = None
            self.game_name = None
            self.language = None

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
                    logger.info("[StreamMetadata] 🔑 Token renouvelé")
                    return
            except Exception as e:
                if tentative < MAX_RETRIES - 1:
                    logger.warning(f"[StreamMetadata] ⚠️ Tentative {tentative + 1}/{MAX_RETRIES} échouée, retry dans {RETRY_BACKOFF_SEC}s: {e}")
                    await asyncio.sleep(RETRY_BACKOFF_SEC * (tentative + 1))
                else:
                    logger.error(f"[StreamMetadata] ❌ Toutes les tentatives de renouvellement token épuisées: {e}")
                    self._app_token = None

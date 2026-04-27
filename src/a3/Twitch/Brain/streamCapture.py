# src/a3/Twitch/Brain/streamCapture.py
#
# Capture le flux vidéo du stream via streamlink et découpe des clips avec ffmpeg.
# Utilise un buffer circulaire de segments pour permettre le clip rétroactif.
import asyncio
import logging
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("A3.StreamCapture")

_BASE = Path(__file__).resolve().parents[4]
BUFFER_DUREE_MAX_SEC = 360
DUREE_SEGMENT_SEC = 30
DELAI_CHAT_VIDEO_SEC = 8
QUALITE_STREAM = "best"
DOSSIER_SEGMENTS = _BASE / "buffer_segments"
DOSSIER_CLIPS = _BASE / "clips_output"


@dataclass
class Segment:
    chemin: Path
    timestamp_debut: float
    timestamp_fin: float
    duree: float

    @property
    def datetime_debut(self) -> datetime:
        return datetime.fromtimestamp(self.timestamp_debut)


class StreamCapture:
    def __init__(self, channel: str):
        self.channel = channel
        self.url_stream = f"https://www.twitch.tv/{channel}"
        self.buffer: deque[Segment] = deque()
        self._lock = threading.Lock()
        self._actif = False
        self._thread: threading.Thread | None = None
        self._surveillance_task: asyncio.Task | None = None

        DOSSIER_SEGMENTS.mkdir(exist_ok=True)
        DOSSIER_CLIPS.mkdir(exist_ok=True)

    async def demarrer(self):
        if self._actif:
            return
        self._actif = True
        self._thread = threading.Thread(target=self._boucle_capture_thread, daemon=True)
        self._thread.start()
        self._surveillance_task = asyncio.create_task(self._surveiller_nouveaux_segments())
        logger.info(f"[StreamCapture] 🎥 Capture démarrée pour {self.channel}")

    async def arreter(self):
        self._actif = False
        if self._surveillance_task:
            self._surveillance_task.cancel()
            try:
                await self._surveillance_task
            except asyncio.CancelledError:
                pass
        self._nettoyer_buffer_complet()
        logger.info("[StreamCapture] 🛑 Capture arrêtée")

    def _boucle_capture_thread(self):
        while self._actif:
            try:
                self._capturer_segments()
            except Exception as e:
                logger.error(f"[StreamCapture] Erreur thread: {e}")
                time.sleep(15)

    def _capturer_segments(self):
        pattern_sortie = str(DOSSIER_SEGMENTS / "seg_%Y%m%d_%H%M%S.ts")
        cmd_streamlink = ["streamlink", "--stdout", "--twitch-disable-ads", self.url_stream, QUALITE_STREAM]
        cmd_ffmpeg = ["ffmpeg", "-i", "pipe:0", "-c", "copy", "-f", "segment", "-segment_time", str(DUREE_SEGMENT_SEC), "-strftime", "1", "-reset_timestamps", "1", pattern_sortie, "-y", "-loglevel", "error"]

        logger.info("[StreamCapture] 🔴 Connexion au stream...")
        proc_sl = subprocess.Popen(cmd_streamlink, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        proc_ff = subprocess.Popen(cmd_ffmpeg, stdin=proc_sl.stdout, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        proc_sl.stdout.close()

        while self._actif and proc_ff.poll() is None:
            time.sleep(1)

        if proc_sl.poll() is not None and proc_sl.returncode != 0:
            logger.warning("[StreamCapture] ⚠️ streamlink a échoué (code=%d), reconnexion dans 20s...", proc_sl.returncode)
            time.sleep(20)
        else:
            # Petit délai avant reconnection pour éviter la boucle serrée
            time.sleep(5)
        try:
            if proc_sl.poll() is None:
                proc_sl.kill()
            if proc_ff.poll() is None:
                proc_ff.kill()
        except Exception as e:
            logger.debug(f"[StreamCapture] Processus déjà arrêtés: {e}")

    async def _surveiller_nouveaux_segments(self):
        vus = set()
        while self._actif:
            try:
                for fichier in sorted(DOSSIER_SEGMENTS.glob("seg_*.ts")):
                    if fichier not in vus:
                        await asyncio.sleep(2)
                        if not fichier.exists():
                            continue
                        taille_avant = fichier.stat().st_size
                        await asyncio.sleep(1)
                        if fichier.exists() and fichier.stat().st_size == taille_avant and taille_avant > 0:
                            vus.add(fichier)
                            self._enregistrer_segment(fichier)
                self._purger_vieux_segments()
            except Exception as e:
                logger.warning(f"[StreamCapture] Surveillance erreur: {e}")
            await asyncio.sleep(5)

    def _enregistrer_segment(self, chemin: Path):
        try:
            nom = chemin.stem
            parties = nom.split("_")
            dt = datetime.strptime(f"{parties[1]}_{parties[2]}", "%Y%m%d_%H%M%S")
            ts_debut = dt.timestamp()
            segment = Segment(chemin, ts_debut, ts_debut + DUREE_SEGMENT_SEC, DUREE_SEGMENT_SEC)
            with self._lock:
                self.buffer.append(segment)
        except Exception:
            logger.warning(f"[StreamCapture] Segment ignoré (parse error): {chemin}")

    def _purger_vieux_segments(self):
        limite = time.time() - BUFFER_DUREE_MAX_SEC
        with self._lock:
            while self.buffer and self.buffer[0].timestamp_fin < limite:
                vieux = self.buffer.popleft()
                try:
                    vieux.chemin.unlink()
                except Exception as e:
                    logger.debug(f"[StreamCapture] Segment cleanup error: {e}")

    def _nettoyer_buffer_complet(self):
        with self._lock:
            for seg in list(self.buffer):
                try:
                    seg.chemin.unlink()
                except Exception as e:
                    logger.debug(f"[StreamCapture] Segment cleanup error: {e}")
            self.buffer.clear()
        for f in DOSSIER_SEGMENTS.glob("seg_*.ts"):
            try:
                f.unlink()
            except Exception:
                pass

    # ── GÉNÉRATION DYNAMIQUE ─────────────────

    async def clip_dynamique(self, ts_debut: float, ts_fin: float, nom: str) -> dict | None:
        if ".." in nom or "/" in nom or "\\" in nom:
            logger.error(f"[StreamCapture] ⚠️ Nom de clip invalide (path traversal): {nom}")
            return None

        ts_debut_reel = ts_debut - DELAI_CHAT_VIDEO_SEC
        ts_fin_reel = ts_fin - DELAI_CHAT_VIDEO_SEC
        duree_totale = ts_fin_reel - ts_debut_reel

        with self._lock:
            segments_necessaires = [s for s in self.buffer if s.timestamp_fin > ts_debut_reel and s.timestamp_debut < ts_fin_reel]

        if not segments_necessaires:
            return None

        chemin_sortie = DOSSIER_CLIPS / nom
        chemin_liste = DOSSIER_SEGMENTS / f"_concat_{nom}.txt"

        with open(chemin_liste, "w", encoding="utf-8") as f:
            for seg in segments_necessaires:
                chemin_safe = str(seg.chemin.resolve()).replace("\\", "/")
                f.write(f"file '{chemin_safe}'\n")

        offset_debut = max(0.0, ts_debut_reel - segments_necessaires[0].timestamp_debut)

        cmd_main = ["ffmpeg", "-f", "concat", "-safe", "0", "-i", str(chemin_liste), "-ss", str(offset_debut), "-t", str(duree_totale), "-c", "copy", str(chemin_sortie), "-y", "-loglevel", "error"]

        nom_stem = Path(nom).stem
        chemin_preview_pattern = DOSSIER_CLIPS / f"preview_{nom_stem}_%03d.mp4"

        cmd_preview = [
            "ffmpeg",
            "-i",
            str(chemin_sortie),
            "-vf",
            "scale=-2:480",
            "-c:v",
            "libx264",
            "-crf",
            "28",
            "-preset",
            "veryfast",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            "-f",
            "segment",
            "-segment_time",
            "60",
            "-reset_timestamps",
            "1",
            str(chemin_preview_pattern),
            "-y",
            "-loglevel",
            "error",
        ]

        loop = asyncio.get_event_loop()

        # Lancer les deux ffmpeg en parallèle
        async def _run_ffmpeg(cmd: list[str], timeout_s: int, label: str) -> bool:
            try:
                await asyncio.wait_for(
                    loop.run_in_executor(None, lambda: subprocess.run(cmd, timeout=timeout_s)),
                    timeout=timeout_s + 20,
                )
                return True
            except asyncio.TimeoutError:
                logger.error(f"[StreamCapture] ⏱️ timeout ffmpeg {label} ({timeout_s}s)")
                return False

        # D'abord le clip principal, puis les previews (qui dépendent du fichier généré)
        ok_main = await _run_ffmpeg(cmd_main, 300, "clip principal")
        if not ok_main:
            logger.error("[StreamCapture] ❌ Échec génération clip principal")
            try:
                chemin_liste.unlink()
            except Exception:
                pass
            return None

        ok_preview = await _run_ffmpeg(cmd_preview, 300, "previews")

        if not ok_preview:
            logger.warning("[StreamCapture] ⚠️ Échec génération previews — clip quand même créé")

        taille_mb = chemin_sortie.stat().st_size / 1024 / 1024
        logger.info(f"[StreamCapture] ✅ Clip HQ généré: {chemin_sortie} ({taille_mb:.1f} MB)")

        previews = [p for p in DOSSIER_CLIPS.iterdir() if p.name.startswith(f"preview_{nom_stem}_") and p.suffix == ".mp4"]
        previews.sort()
        logger.info(f"[StreamCapture] 🔍 {len(previews)} morceau(x) de preview trouvé(s)")

        try:
            chemin_liste.unlink()
        except Exception:
            pass

        return {"hq": chemin_sortie, "previews": previews}

    def etat_buffer(self) -> dict:
        if not self.buffer:
            return {"segments": 0, "duree_totale_sec": 0}
        return {"segments": len(self.buffer), "plus_vieux": self.buffer[0].datetime_debut.strftime("%H:%M:%S"), "plus_recent": self.buffer[-1].datetime_debut.strftime("%H:%M:%S")}

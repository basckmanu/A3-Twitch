# src/a3/Twitch/multi.py
#
# Launcher multi-processus : un process par channel Twitch.
# Chaque process est complètement indépendant (capture, watcher, brain, renderer).

import argparse
import logging
import multiprocessing as mp
import signal
import sys
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("A3")

# Ajoute src au path pour les imports
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from a3.config import CHANNELS  # noqa: E402


def _run_channel(channel: str, log_dir: Path) -> None:
    """Fonction exécutée dans un subprocess pour un channel donné."""
    from a3.Twitch.mainTwitch import TwitchBot

    # Setup logging propre au subprocess
    log_file = log_dir / f"a3_{channel}_{datetime.now():%Y-%m-%d_%H-%M-%S}.log"
    logger = logging.getLogger(f"A3.{channel}")
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(ch)

    logger.info(f"[Multi] 🚀 Process démarré pour #{channel}")

    try:
        bot = TwitchBot(logger, single_channel=channel)
        bot.run()
    except KeyboardInterrupt:
        logger.info(f"[Multi] ⛔ Arrêt demandé pour #{channel}")
    finally:
        logger.info(f"[Multi] 🛑 Process terminé pour #{channel}")


def _setup_log_dir(base: Path) -> Path:
    d = base / "logs"
    d.mkdir(exist_ok=True)
    return d


def launch_multi(channels: list[str] | None = None) -> None:
    """Lance un process par channel."""
    if channels is None:
        channels = CHANNELS

    if not channels:
        logger.warning("[Multi] ⚠️  Aucun channel défini dans CHANNELS")
        return

    base = Path(__file__).resolve().parents[2]
    log_dir = _setup_log_dir(base)

    processes: list[mp.Process] = []

    for channel in channels:
        p = mp.Process(target=_run_channel, args=(channel, log_dir), name=f"a3-{channel}", daemon=False)
        p.start()
        processes.append(p)
        logger.info(f"[Multi] ✅ Process #{p.name} (pid={p.pid}) démarré")

    def shutdown_all(signum, frame):
        logger.info("\n[Multi] ⛔ Arrêt global demandé...")
        for p in processes:
            p.terminate()
        for p in processes:
            p.join(timeout=5)
            if p.is_alive():
                p.kill()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown_all)
    signal.signal(signal.SIGTERM, shutdown_all)

    try:
        for p in processes:
            p.join()
    except KeyboardInterrupt:
        shutdown_all(None, None)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="A3 Multi-Channel Launcher")
    parser.add_argument("--channels", nargs="+", help="Channels à lancer (sinon lit CHANNELS depuis .env)")
    args = parser.parse_args()

    launch_multi(channels=args.channels)

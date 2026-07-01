# src/a3/Twitch/Renderer/mainRendererTwitch.py

import asyncio
import json
import logging
import os
import shutil
import time
from datetime import datetime
from pathlib import Path

import discord

from a3.config import DISCORD_BOT_TOKEN
from a3.config import DISCORD_CHANNEL_ID as _DISCORD_CHANNEL_ID_STR

log = logging.getLogger("A3")

_BASE = Path(__file__).resolve().parents[3]

def _CHANNEL(channel: str, sub: str) -> Path:
    return _BASE / "clips" / channel / sub

# ------------------------------------------------------------------ #
#  Configuration                                                     #
# ------------------------------------------------------------------ #

try:
    DISCORD_CHANNEL_ID = int(_DISCORD_CHANNEL_ID_STR or 0)
except ValueError:
    DISCORD_CHANNEL_ID = 0
    log.warning("[Renderer] ⚠️ DISCORD_CHANNEL_ID invalide, mis à 0")
DISCORD_ALLOWED_USERS: set[int] = {
    int(uid.strip()) for uid in os.getenv("DISCORD_ALLOWED_USERS", "").split(",")
    if uid.strip().isdigit()
}

FICHIER_BLACKLIST = _BASE / "blacklist_mots.json"
FICHIER_PENDING = _BASE / "pending_reviews.json"

TAILLE_MAX_MB = 8.0

# ------------------------------------------------------------------ #
#  Blacklist                                                         #
# ------------------------------------------------------------------ #


def charger_blacklist() -> set[str]:
    if not FICHIER_BLACKLIST.exists():
        FICHIER_BLACKLIST.write_text("[]", encoding="utf-8")
        return set()
    try:
        with open(FICHIER_BLACKLIST, encoding="utf-8") as f:
            return set(json.load(f))
    except Exception as e:
        import logging
        logging.getLogger("A3").warning(f"[Renderer] ⚠️ Blacklist load failed: {e}")
        return set()


# ------------------------------------------------------------------ #
#  Pending reviews (persistance entre redémarrages)                  #
# ------------------------------------------------------------------ #

def _lire_pending() -> list[dict]:
    if not FICHIER_PENDING.exists():
        return []
    try:
        with open(FICHIER_PENDING, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def _ecrire_pending(clips: list[dict]) -> None:
    try:
        with open(FICHIER_PENDING, "w", encoding="utf-8") as f:
            json.dump(clips, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.warning(f"[Renderer] ⚠️ Impossible d'écrire pending_reviews.json: {e}")

def _ajouter_pending(clip_num: int, chemin_clip: str, channel: str, mot_repetition: str | None) -> None:
    clips = _lire_pending()
    clips = [c for c in clips if c.get("clip_num") != clip_num]
    clips.append({"clip_num": clip_num, "chemin_clip": chemin_clip, "channel": channel, "mot_repetition": mot_repetition})
    _ecrire_pending(clips)

def _retirer_pending(clip_num: int) -> None:
    clips = _lire_pending()
    clips = [c for c in clips if c.get("clip_num") != clip_num]
    _ecrire_pending(clips)


# ------------------------------------------------------------------ #
#  Boutons de review                                                 #
# ------------------------------------------------------------------ #


class ClipView(discord.ui.View):
    def __init__(
        self,
        channel: str,
        chemin_clip: str,
        clip_num: int,
        decision_logger=None,
        mot_repetition: str | None = None,
        structured_logger=None,
    ) -> None:
        super().__init__(timeout=None)
        self.channel = channel
        self.chemin_clip = Path(chemin_clip) if chemin_clip else None
        self.clip_num = clip_num
        self.decision_logger = decision_logger
        self.mot_repetition = mot_repetition
        self._struct_log = structured_logger
        self._sent_at: float = time.time()

        btn_garder: discord.ui.Button = discord.ui.Button(
            label="✅ Garder", style=discord.ButtonStyle.success,
            custom_id=f"garder_{clip_num}"
        )
        btn_garder.callback = self.garder  # type: ignore[assignment, method-assign]
        self.add_item(btn_garder)

        btn_highlight: discord.ui.Button = discord.ui.Button(
            label="⭐ Highlight", style=discord.ButtonStyle.primary,
            custom_id=f"highlight_{clip_num}"
        )
        btn_highlight.callback = self.highlight  # type: ignore[assignment, method-assign]
        self.add_item(btn_highlight)

        btn_supprimer: discord.ui.Button = discord.ui.Button(
            label="🗑️ Supprimer", style=discord.ButtonStyle.danger,
            custom_id=f"supprimer_{clip_num}"
        )
        btn_supprimer.callback = self.supprimer  # type: ignore[assignment, method-assign]
        self.add_item(btn_supprimer)

    def _dest_dir(self, sub: str) -> Path:
        d = _CHANNEL(self.channel, sub)
        d.mkdir(parents=True, exist_ok=True)
        return d

    async def garder(self, interaction: discord.Interaction) -> None:
        if DISCORD_ALLOWED_USERS and interaction.user.id not in DISCORD_ALLOWED_USERS:
            await interaction.response.send_message("⛔ Pas autorisé.", ephemeral=True)
            return
        # Accuser réception tout de suite — Discord n'accorde que 3s pour la première
        # réponse, et le déplacement du fichier vidéo (dizaines de Mo) peut dépasser ce
        # délai (antivirus, I/O disque...), ce qui faisait échouer le clic silencieusement.
        await interaction.response.defer()
        try:
            chemin_dest = self._deplacer("validated")
            if chemin_dest:
                _retirer_pending(self.clip_num)
                if self.decision_logger:
                    self.decision_logger.log_decision(self.clip_num, "garder", interaction.user.name)
                if self._struct_log:
                    self._struct_log.log_review(self.clip_num, "garder", interaction.user.name, 0,
                                                reaction_time_sec=round(time.time() - self._sent_at, 1))
                await interaction.edit_original_response(
                    content=(interaction.message.content if interaction.message else "") + f"\n\n✅ **Gardé** par {interaction.user.name} → `{chemin_dest}`",
                    view=None,
                )
                log.info(f"[Renderer] ✅ Clip #{self.clip_num} gardé → {chemin_dest}")
            else:
                await interaction.followup.send("⚠️ Fichier introuvable ou déjà traité.", ephemeral=True)
        except Exception as exc:
            log.error(f"[Renderer] erreur dans garder() → {exc}")

    async def highlight(self, interaction: discord.Interaction) -> None:
        if DISCORD_ALLOWED_USERS and interaction.user.id not in DISCORD_ALLOWED_USERS:
            await interaction.response.send_message("⛔ Pas autorisé.", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            chemin_dest = self._deplacer("highlights")
            if chemin_dest:
                _retirer_pending(self.clip_num)
                if self.decision_logger:
                    self.decision_logger.log_decision(self.clip_num, "highlight", interaction.user.name)
                if self._struct_log:
                    self._struct_log.log_review(self.clip_num, "highlight", interaction.user.name, 0,
                                                reaction_time_sec=round(time.time() - self._sent_at, 1))
                await interaction.edit_original_response(
                    content=(interaction.message.content if interaction.message else "") + f"\n\n⭐ **Highlight** par {interaction.user.name} → `{chemin_dest}`",
                    view=None,
                )
                log.info(f"[Renderer] ⭐ Clip #{self.clip_num} marqué highlight → {chemin_dest}")
            else:
                await interaction.followup.send("⚠️ Fichier introuvable ou déjà traité.", ephemeral=True)
        except Exception as exc:
            log.error(f"[Renderer] erreur dans highlight() → {exc}")

    async def supprimer(self, interaction: discord.Interaction) -> None:
        if DISCORD_ALLOWED_USERS and interaction.user.id not in DISCORD_ALLOWED_USERS:
            await interaction.response.send_message("⛔ Pas autorisé.", ephemeral=True)
            return
        try:
            await interaction.response.send_message("🗑️ Suppression en cours...", ephemeral=True)
        except Exception:
            pass
        try:
            if self.decision_logger:
                self.decision_logger.log_decision(self.clip_num, "supprimer", interaction.user.name)
            if self._struct_log:
                self._struct_log.log_review(self.clip_num, "supprimer", interaction.user.name, 0,
                                            reaction_time_sec=round(time.time() - self._sent_at, 1))
        except Exception as exc:
            log.error(f"[Renderer] erreur log_review/log_decision → {exc}")
        try:
            _retirer_pending(self.clip_num)
            self._supprimer()
            if interaction.message:
                await interaction.message.delete()
            log.info(f"[Renderer] 🗑️ Clip #{self.clip_num} supprimé par {interaction.user.name}")
        except Exception as exc:
            log.error(f"[Renderer] erreur dans _supprimer/delete → {exc}")

    def _deplacer(self, sub: str) -> Path | None:
        if not self.chemin_clip or not self.chemin_clip.exists():
            return None
        dossier = self._dest_dir(sub)
        dest = dossier / self.chemin_clip.name
        shutil.move(str(self.chemin_clip), dest)
        return dest

    def _supprimer(self) -> None:
        if self.chemin_clip and self.chemin_clip.exists():
            dossier = self._dest_dir("rejected")
            dest = dossier / self.chemin_clip.name
            try:
                shutil.move(str(self.chemin_clip), dest)
                log.info(f"[Renderer] _supprimer: déplacé vers {dest}")
            except Exception as exc:
                log.error(f"[Renderer] _supprimer: erreur shutil.move → {exc}")
        else:
            log.warning(f"[Renderer] _supprimer: fichier introuvable — {self.chemin_clip}")


# ------------------------------------------------------------------ #
#  Renderer                                                          #
# ------------------------------------------------------------------ #


class Renderer:
    def __init__(self, channel: str, decision_logger=None, struct_log=None) -> None:
        self._client: discord.Client | None = None
        self._channel: discord.TextChannel | None = None
        self._ready = asyncio.Event()
        self._clip_counter: int = 0
        self.decision_logger = decision_logger
        self._struct_log = struct_log
        self.channel = channel

    def _clip_dir(self, sub: str) -> Path:
        return _CHANNEL(self.channel, sub)

    async def start(self) -> None:
        intents = discord.Intents.default()
        self._client = discord.Client(intents=intents)

        # Pré-enregistrer les views persistantes pour les clips en attente de review
        pending = _lire_pending()
        for entry in pending:
            view = ClipView(
                channel=entry.get("channel", self.channel),
                chemin_clip=entry.get("chemin_clip", ""),
                clip_num=entry["clip_num"],
                decision_logger=self.decision_logger,
                mot_repetition=entry.get("mot_repetition"),
                structured_logger=self._struct_log,
            )
            self._client.add_view(view)
        if pending:
            log.info(f"[Renderer] 🔄 {len(pending)} view(s) persistante(s) rechargée(s)")

        @self._client.event
        async def on_ready():
            self._channel = self._client.get_channel(DISCORD_CHANNEL_ID)
            if self._channel:
                log.info(f"[Renderer] ✅ Bot Discord connecté — channel : #{self._channel.name}")
            else:
                log.warning("[Renderer] ⚠️ Channel introuvable, vérifie DISCORD_CHANNEL_ID")
            self._ready.set()

        asyncio.create_task(self._client.start(DISCORD_BOT_TOKEN or ""))

        try:
            await asyncio.wait_for(self._ready.wait(), timeout=15.0)
        except asyncio.TimeoutError:
            log.warning("[Renderer] ⚠️ Timeout connexion Discord — les clips seront envoyés dès que possible")

    async def output(self, données: dict) -> None:
        await self._ready.wait()

        if not self._channel:
            log.warning("[Renderer] ⚠️ Pas de channel Discord, clip non envoyé")
            return

        clip_num = données.get("clip_num") or (self._clip_counter + 1)
        self._clip_counter = clip_num

        score = données.get("score_final", 0.0)
        timestamp = données.get("timestamp", datetime.now())
        chemin_hq = données.get("chemin_clip")
        previews = données.get("chemins_previews", [])
        message = données.get("message")
        détails = données.get("détails", {})
        mot_rep = données.get("mot_repetition")

        auteur = données.get("auteur_trigger") or (message.author.name if message else "inconnu")
        streamer = données.get("channel") or self.channel or "inconnu"
        contenu_msg = données.get("message_content") or (message.content[:80] if message else "")
        heure = timestamp.strftime("%H:%M:%S")

        filtres_actifs = [f"`{nom}` ({v.get('score_pondéré', 0):.2f})" for nom, v in détails.items() if v.get("score_pondéré", 0) > 0]

        contenu = f"🎬 **Clip #{clip_num}** — **{streamer}** — {heure}\nScore : **{score:.2f}** | Déclenché par : `{auteur}` : *{contenu_msg}*\nFiltres : {', '.join(filtres_actifs) or 'aucun'}\n📁 `{chemin_hq}`"
        if mot_rep:
            contenu += f"\n🔤 Mot répété : `{mot_rep}`"

        # Sauvegarder avant envoi pour que les boutons survivent aux redémarrages
        if chemin_hq:
            _ajouter_pending(clip_num, chemin_hq, self.channel, mot_rep)

        view = ClipView(
            chemin_clip=chemin_hq or "",
            clip_num=clip_num,
            decision_logger=self.decision_logger,
            mot_repetition=mot_rep,
            structured_logger=self._struct_log,
            channel=self.channel,
        )
        # Enregistrer la view pour que les interactions fonctionnent immédiatement
        if self._client:
            self._client.add_view(view)

        if previews:
            await self._envoyer_avec_previews(contenu, previews, view)
        else:
            await self._channel.send(content=contenu + "\n⚠️ _Pas d'aperçu vidéo_", view=view)

    async def _envoyer_avec_previews(self, contenu: str, previews: list, view: ClipView) -> None:
        fichiers = []
        if self._channel is None:
            log.error("[Renderer] ❌ _envoyer_avec_previews appelé sans channel")
            return
        try:
            for p in [Path(p) for p in previews[:10]]:
                if p.exists():
                    taille = p.stat().st_size / 1024 / 1024
                    if taille <= TAILLE_MAX_MB:
                        fichiers.append(discord.File(str(p), filename=p.name))
                    else:
                        log.warning(f"[Renderer] ⚠️ Preview trop lourde ignorée : {p.name} ({taille:.1f} MB)")

            if fichiers:
                await self._channel.send(content=contenu, files=fichiers, view=view)
                log.info(f"[Renderer] ✅ Clip envoyé sur Discord ({len(fichiers)} preview(s))")
            else:
                await self._channel.send(content=contenu + "\n⚠️ _Aperçus trop lourds_", view=view)

        except Exception as e:
            log.error(f"[Renderer] ❌ Erreur envoi Discord : {e}")
            try:
                await self._channel.send(content=contenu + "\n⚠️ _Erreur envoi previews_", view=view)
            except Exception:
                pass
        finally:
            for f in fichiers:
                if hasattr(f, "fp"):
                    f.fp.close()

    async def stop(self) -> None:
        if self._client:
            await self._client.close()
        log.info("[Renderer] 🛑 Bot Discord arrêté")

# src/a3/Twitch/Renderer/mainRendererTwitch.py

import asyncio
import json
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path

import discord

log = logging.getLogger("A3")

# ------------------------------------------------------------------ #
#  Configuration                                                     #
# ------------------------------------------------------------------ #

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))
DISCORD_ALLOWED_USERS = {uid.strip() for uid in os.getenv("DISCORD_ALLOWED_USERS", "").split(",") if uid.strip()}

DOSSIER_VALIDATED = Path("clips_validated")
DOSSIER_HIGHLIGHTS = Path("clips_highlights")
DOSSIER_REJECTED = Path("clips_rejected")
FICHIER_BLACKLIST = Path("blacklist_mots.json")

TAILLE_MAX_MB = 24.0

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
#  Boutons de review                                                 #
# ------------------------------------------------------------------ #


class ClipView(discord.ui.View):
    def __init__(
        self,
        chemin_clip: str,
        clip_num: int,
        decision_logger=None,
        mot_repetition: str | None = None,
    ) -> None:
        super().__init__(timeout=None)
        self.chemin_clip = Path(chemin_clip) if chemin_clip else None
        self.clip_num = clip_num
        self.decision_logger = decision_logger
        self.mot_repetition = mot_repetition

    @discord.ui.button(label="✅ Garder", style=discord.ButtonStyle.success, custom_id="garder")
    async def garder(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if DISCORD_ALLOWED_USERS and interaction.user.id not in DISCORD_ALLOWED_USERS:
            await interaction.response.send_message("⛔ Pas autorisé.", ephemeral=True)
            return
        chemin_dest = self._deplacer(DOSSIER_VALIDATED)
        if chemin_dest:
            if self.decision_logger:
                self.decision_logger.log_decision(self.clip_num, "garder", interaction.user.name)
            await interaction.response.edit_message(
                content=(interaction.message.content if interaction.message else "") + f"\n\n✅ **Gardé** par {interaction.user.name} → `{chemin_dest}`",
                view=None,
            )
            log.info(f"[Renderer] ✅ Clip #{self.clip_num} gardé → {chemin_dest}")
        else:
            await interaction.response.send_message("⚠️ Fichier introuvable ou déjà traité.", ephemeral=True)

    @discord.ui.button(label="⭐ Highlight", style=discord.ButtonStyle.primary, custom_id="highlight")
    async def highlight(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if DISCORD_ALLOWED_USERS and interaction.user.id not in DISCORD_ALLOWED_USERS:
            await interaction.response.send_message("⛔ Pas autorisé.", ephemeral=True)
            return
        chemin_dest = self._deplacer(DOSSIER_HIGHLIGHTS)
        if chemin_dest:
            if self.decision_logger:
                self.decision_logger.log_decision(self.clip_num, "highlight", interaction.user.name)
            await interaction.response.edit_message(
                content=(interaction.message.content if interaction.message else "") + f"\n\n⭐ **Highlight** par {interaction.user.name} → `{chemin_dest}`",
                view=None,
            )
            log.info(f"[Renderer] ⭐ Clip #{self.clip_num} marqué highlight → {chemin_dest}")
        else:
            await interaction.response.send_message("⚠️ Fichier introuvable ou déjà traité.", ephemeral=True)

    @discord.ui.button(label="🗑️ Supprimer", style=discord.ButtonStyle.danger, custom_id="supprimer")
    async def supprimer(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if DISCORD_ALLOWED_USERS and interaction.user.id not in DISCORD_ALLOWED_USERS:
            await interaction.response.send_message("⛔ Pas autorisé.", ephemeral=True)
            return
        if self.decision_logger:
            self.decision_logger.log_decision(self.clip_num, "supprimer", interaction.user.name)
        self._supprimer()
        if interaction.message:
            await interaction.message.delete()
        log.info(f"[Renderer] 🗑️ Clip #{self.clip_num} supprimé par {interaction.user.name}")

    def _deplacer(self, dossier: Path) -> Path | None:
        if not self.chemin_clip or not self.chemin_clip.exists():
            return None
        dossier.mkdir(exist_ok=True)
        dest = dossier / self.chemin_clip.name
        shutil.move(str(self.chemin_clip), dest)
        return dest

    def _supprimer(self) -> None:
        if self.chemin_clip and self.chemin_clip.exists():
            DOSSIER_REJECTED.mkdir(exist_ok=True)
            dest = DOSSIER_REJECTED / self.chemin_clip.name
            shutil.move(str(self.chemin_clip), dest)


# ------------------------------------------------------------------ #
#  Renderer                                                          #
# ------------------------------------------------------------------ #


class Renderer:
    def __init__(self, decision_logger=None) -> None:
        self._client: discord.Client | None = None
        self._channel: discord.TextChannel | None = None
        self._ready = asyncio.Event()
        self._clip_counter: int = 0
        self.decision_logger = decision_logger

    async def start(self) -> None:
        intents = discord.Intents.default()
        self._client = discord.Client(intents=intents)

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

        self._clip_counter += 1
        clip_num = self._clip_counter

        score = données.get("score_final", 0.0)
        timestamp = données.get("timestamp", datetime.now())
        chemin_hq = données.get("chemin_clip")
        previews = données.get("chemins_previews", [])
        message = données.get("message")
        détails = données.get("détails", {})
        mot_rep = données.get("mot_repetition")

        auteur = message.author.name if message else "inconnu"
        contenu_msg = message.content[:80] if message else ""
        heure = timestamp.strftime("%H:%M:%S")

        filtres_actifs = [f"`{nom}` ({v.get('score_pondéré', 0):.2f})" for nom, v in détails.items() if v.get("score_pondéré", 0) > 0]

        # Log du clip dans le fichier de décisions
        if self.decision_logger:
            self.decision_logger.log_clip(
                clip_num=clip_num,
                score=score,
                filtres=détails,
                chemin=chemin_hq,
                mot_repetition=mot_rep,
            )

        contenu = f"🎬 **Clip #{clip_num}** — {heure}\nScore : **{score:.2f}** | Déclenché par : `{auteur}` : *{contenu_msg}*\nFiltres : {', '.join(filtres_actifs) or 'aucun'}\n📁 `{chemin_hq}`"
        if mot_rep:
            contenu += f"\n🔤 Mot répété : `{mot_rep}`"

        view = ClipView(
            chemin_clip=chemin_hq or "",
            clip_num=clip_num,
            decision_logger=self.decision_logger,
            mot_repetition=mot_rep,
        )

        if previews:
            await self._envoyer_avec_previews(contenu, previews, view)
        else:
            await self._channel.send(content=contenu + "\n⚠️ _Pas d'aperçu vidéo_", view=view)

    async def _envoyer_avec_previews(self, contenu: str, previews: list, view: ClipView) -> None:
        fichiers = []
        assert self._channel is not None, "_envoyer_avec_previews appelé sans channel"
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

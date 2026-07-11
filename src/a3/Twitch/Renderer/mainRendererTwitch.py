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

from a3.config import BASE_DIR as _BASE
from a3.config import DISCORD_BOT_TOKEN
from a3.config import DISCORD_CHANNEL_ID as _DISCORD_CHANNEL_ID_STR
from a3.utils.privacy import pseudonymize

log = logging.getLogger("A3")

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
if not DISCORD_ALLOWED_USERS:
    log.warning(
        "[Renderer] ⚠️ DISCORD_ALLOWED_USERS non configuré — TOUT utilisateur ayant accès "
        "au salon Discord peut cliquer Garder/Highlight/Supprimer sur les clips. "
        "Renseigne DISCORD_ALLOWED_USERS (IDs Discord séparés par des virgules) dans .env "
        "pour restreindre la review aux personnes autorisées."
    )

FICHIER_BLACKLIST = _BASE / "blacklist_mots.json"
FICHIER_PENDING = _BASE / "pending_reviews.json"

TAILLE_MAX_MB = 8.0
RAPPEL_DELAI_SEC = 30 * 60           # sans décision après 30 min → rappel Discord
AUTO_EXPIRE_DELAI_SEC = 24 * 60 * 60  # sans décision après 1j → auto-rejet ("expire")
VERIF_PENDING_INTERVAL_SEC = 5 * 60  # fréquence de vérification des clips en attente
RAISON_TIMEOUT_SEC = 15 * 60  # bouton cliqué mais raison jamais choisie → finalise quand même

# ------------------------------------------------------------------ #
#  Raisons de garder/highlight/supprimer (pour l'analyse des données) #
# ------------------------------------------------------------------ #

SOUS_DOSSIER_ACTION = {"garder": "validated", "highlight": "highlights", "supprimer": "rejected"}
LABEL_ACTION = {"garder": "✅ Gardé", "highlight": "⭐ Highlight", "supprimer": "🗑️ Supprimé"}
VERBE_ACTION = {"garder": "garder", "highlight": "mettre en highlight", "supprimer": "supprimer"}

# Catégories courtes et stables (stockées telles quelles en base, dans reviews.reason)
# — un menu déroulant plutôt qu'un champ texte libre : un seul clic supplémentaire,
# et des valeurs propres, agrégeables en SQL (vs du texte libre inexploitable).
RAISONS_VALIDATION: list[tuple[str, str]] = [
    ("hype", "🔥 Moment fort / hype authentique"),
    ("jeu", "🎮 Jeu compétitif (clutch, exploit, gros play)"),
    ("drole", "😂 Moment drôle / troll"),
    ("fail", "💀 Fail / rage mémorable"),
    ("chat", "💬 Interaction chat forte (spam, mème collectif)"),
    ("autre_validation", "❓ Autre raison"),
]

RAISONS_REJET: list[tuple[str, str]] = [
    ("faux_positif", "🚫 Faux positif — rien de particulier"),
    ("pas_assez_fort", "📉 Hype réelle mais pas assez forte pour un clip"),
    ("doublon", "🔁 Doublon / déjà clippé"),
    ("technique", "⚙️ Problème technique (son, image, coupure)"),
    ("sensible", "🔒 Contenu sensible / à ne pas partager"),
    ("autre_rejet", "❓ Autre raison"),
]

ANNULER_VALUE = "__annuler__"


def _raisons_pour(action: str) -> list[tuple[str, str]]:
    return RAISONS_VALIDATION if action in ("garder", "highlight") else RAISONS_REJET


def _label_raison(action: str, code: str) -> str:
    for cle, label in _raisons_pour(action):
        if cle == code:
            return label
    return code

# ------------------------------------------------------------------ #
#  Déplacement de fichier clip (partagé entre ClipView et l'auto-expire) #
# ------------------------------------------------------------------ #

def _dest_dir(channel: str, sub: str) -> Path:
    d = _CHANNEL(channel, sub)
    d.mkdir(parents=True, exist_ok=True)
    return d

def _deplacer_fichier(chemin_clip: Path | None, channel: str, sub: str) -> Path | None:
    if not chemin_clip or not chemin_clip.exists():
        return None
    dossier = _dest_dir(channel, sub)
    dest = dossier / chemin_clip.name
    shutil.move(str(chemin_clip), dest)
    return dest

def _format_duree(sec: float) -> str:
    if sec % 86400 == 0:
        return f"{int(sec // 86400)}j"
    return f"{sec / 3600:.0f}h"

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
    # clip_num n'est unique que PAR channel (chaque stream numérote ses clips depuis 1) —
    # la clé d'identification doit donc être le couple (channel, clip_num), jamais clip_num seul.
    clips = _lire_pending()
    clips = [c for c in clips if not (c.get("clip_num") == clip_num and c.get("channel") == channel)]
    clips.append({
        "clip_num": clip_num, "chemin_clip": chemin_clip, "channel": channel, "mot_repetition": mot_repetition,
        "envoye_at": time.time(), "message_id": None, "rappel_envoye": False,
        # Renseignés une fois qu'un bouton garder/highlight/supprimer est cliqué, tant
        # que la raison n'est pas encore choisie — permet de reconstruire le bon menu
        # déroulant (plutôt que les boutons) si le bot redémarre dans cette fenêtre.
        "action_en_attente": None, "reviewer_hash_en_attente": None, "action_choisie_at": None,
    })
    _ecrire_pending(clips)

def _definir_action_en_attente(clip_num: int, channel: str, action: str | None, reviewer_hash: str | None) -> None:
    clips = _lire_pending()
    for c in clips:
        if c.get("clip_num") == clip_num and c.get("channel") == channel:
            c["action_en_attente"] = action
            c["reviewer_hash_en_attente"] = reviewer_hash
            c["action_choisie_at"] = time.time() if action else None
            break
    _ecrire_pending(clips)

def _retirer_pending(clip_num: int, channel: str) -> None:
    clips = _lire_pending()
    clips = [c for c in clips if not (c.get("clip_num") == clip_num and c.get("channel") == channel)]
    _ecrire_pending(clips)

def _definir_message_id(clip_num: int, channel: str, message_id: int) -> None:
    clips = _lire_pending()
    for c in clips:
        if c.get("clip_num") == clip_num and c.get("channel") == channel:
            c["message_id"] = message_id
            break
    _ecrire_pending(clips)


# ------------------------------------------------------------------ #
#  Finalisation d'une décision (partagée entre le menu déroulant et   #
#  l'auto-résolution des clips restés bloqués sans raison choisie)    #
# ------------------------------------------------------------------ #

def _appliquer_decision(
    action: str,
    raison: str,
    channel: str,
    chemin_clip: Path | None,
    clip_num: int,
    decision_logger,
    struct_log,
    reviewer_name: str,
    reviewer_is_hash: bool,
    sent_at: float,
    *,
    retirer_du_pending: bool = True,
) -> Path | None:
    """Déplace le fichier, journalise la décision (+ raison) en DB et dans decisions/.
    Utilisé à la fois par le menu déroulant (chemin interactif) et par l'auto-
    résolution des clips restés bloqués sans raison (voir RAISON_TIMEOUT_SEC)."""
    chemin_dest = _deplacer_fichier(chemin_clip, channel, SOUS_DOSSIER_ACTION[action])
    if retirer_du_pending:
        _retirer_pending(clip_num, channel)
    if decision_logger:
        decision_logger.log_decision(clip_num, action, reviewer_name, reason=raison, user_is_hash=reviewer_is_hash)
    if struct_log:
        struct_log.log_review(
            clip_num, action, reviewer_name, 0,
            reaction_time_sec=round(time.time() - sent_at, 1),
            reason=raison, channel=channel, user_is_hash=reviewer_is_hash,
            new_file_path=str(chemin_dest) if chemin_dest else None,
        )
    return chemin_dest


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

        # custom_id inclut le channel : clip_num seul n'est unique que par stream, et deux
        # streams simultanés génèrent chacun un "clip #1" — sans le channel, les boutons
        # Discord de deux clips différents collisionnent (le dernier view enregistré gagne
        # le routing des interactions pour ce custom_id).
        btn_garder: discord.ui.Button = discord.ui.Button(
            label="✅ Garder", style=discord.ButtonStyle.success,
            custom_id=f"garder_{channel}_{clip_num}"
        )
        btn_garder.callback = self.garder  # type: ignore[assignment, method-assign]
        self.add_item(btn_garder)

        btn_highlight: discord.ui.Button = discord.ui.Button(
            label="⭐ Highlight", style=discord.ButtonStyle.primary,
            custom_id=f"highlight_{channel}_{clip_num}"
        )
        btn_highlight.callback = self.highlight  # type: ignore[assignment, method-assign]
        self.add_item(btn_highlight)

        btn_supprimer: discord.ui.Button = discord.ui.Button(
            label="🗑️ Supprimer", style=discord.ButtonStyle.danger,
            custom_id=f"supprimer_{channel}_{clip_num}"
        )
        btn_supprimer.callback = self.supprimer  # type: ignore[assignment, method-assign]
        self.add_item(btn_supprimer)

    def _dest_dir(self, sub: str) -> Path:
        return _dest_dir(self.channel, sub)

    async def garder(self, interaction: discord.Interaction) -> None:
        await self._demander_raison(interaction, "garder")

    async def highlight(self, interaction: discord.Interaction) -> None:
        await self._demander_raison(interaction, "highlight")

    async def supprimer(self, interaction: discord.Interaction) -> None:
        await self._demander_raison(interaction, "supprimer")

    async def _demander_raison(self, interaction: discord.Interaction, action: str) -> None:
        """Ne fait pas encore l'action — remplace les boutons par un menu déroulant
        de raisons. L'action réelle (déplacement fichier, DB, decisions/) n'est
        appliquée qu'une fois une raison choisie, dans ReasonView._on_select."""
        if DISCORD_ALLOWED_USERS and interaction.user.id not in DISCORD_ALLOWED_USERS:
            await interaction.response.send_message("⛔ Pas autorisé.", ephemeral=True)
            return

        reviewer_hash = pseudonymize(interaction.user.name) or "unknown"
        _definir_action_en_attente(self.clip_num, self.channel, action, reviewer_hash)

        reason_view = ReasonView(
            channel=self.channel,
            chemin_clip=str(self.chemin_clip) if self.chemin_clip else "",
            clip_num=self.clip_num,
            action=action,
            decision_logger=self.decision_logger,
            mot_repetition=self.mot_repetition,
            structured_logger=self._struct_log,
            reviewer_name=interaction.user.name,
            reviewer_is_hash=False,
            sent_at=self._sent_at,
        )
        if interaction.client:
            interaction.client.add_view(reason_view)

        try:
            # edit_message() = la réponse initiale à l'interaction (rapide, pas de defer
            # nécessaire ici — contrairement à garder/highlight avant, on ne déplace rien).
            await interaction.response.edit_message(view=reason_view)
        except Exception as exc:
            log.error(f"[Renderer] erreur affichage sélecteur de raison ({action}) → {type(exc).__name__}: {exc}")


class ReasonView(discord.ui.View):
    """Menu déroulant affiché après un clic sur Garder/Highlight/Supprimer —
    demande la raison avant d'appliquer réellement la décision."""

    def __init__(
        self,
        channel: str,
        chemin_clip: str,
        clip_num: int,
        action: str,
        decision_logger=None,
        mot_repetition: str | None = None,
        structured_logger=None,
        reviewer_name: str = "unknown",
        reviewer_is_hash: bool = False,
        sent_at: float | None = None,
    ) -> None:
        super().__init__(timeout=None)
        self.channel = channel
        self.chemin_clip = Path(chemin_clip) if chemin_clip else None
        self.clip_num = clip_num
        self.action = action
        self.decision_logger = decision_logger
        self.mot_repetition = mot_repetition
        self._struct_log = structured_logger
        self._reviewer_name = reviewer_name
        self._reviewer_is_hash = reviewer_is_hash
        self._sent_at = sent_at if sent_at is not None else time.time()

        options = [
            discord.SelectOption(label=label, value=cle)
            for cle, label in _raisons_pour(action)
        ]
        options.append(discord.SelectOption(label="↩️ Annuler — revenir aux boutons", value=ANNULER_VALUE))

        select: discord.ui.Select = discord.ui.Select(
            placeholder=f"Pourquoi veux-tu {VERBE_ACTION[action]} ce clip ?",
            custom_id=f"raison_{action}_{channel}_{clip_num}",
            options=options,
        )
        select.callback = self._on_select  # type: ignore[assignment, method-assign]
        self._select = select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        if DISCORD_ALLOWED_USERS and interaction.user.id not in DISCORD_ALLOWED_USERS:
            await interaction.response.send_message("⛔ Pas autorisé.", ephemeral=True)
            return

        raison = self._select.values[0] if self._select.values else ""

        if raison == ANNULER_VALUE:
            await self._revenir_aux_boutons(interaction)
            return

        # Accuser réception tout de suite — le déplacement du fichier vidéo (dizaines
        # de Mo) peut dépasser les 3s accordées par Discord pour la 1ère réponse.
        try:
            await interaction.response.defer()
        except Exception as exc:
            log.error(f"[Renderer] erreur accusé réception raison({self.action}) → {type(exc).__name__}: {exc}")
            return

        try:
            chemin_dest = _appliquer_decision(
                self.action, raison, self.channel, self.chemin_clip, self.clip_num,
                self.decision_logger, self._struct_log,
                self._reviewer_name, self._reviewer_is_hash, self._sent_at,
            )
            label = LABEL_ACTION[self.action]
            raison_label = _label_raison(self.action, raison)
            suffixe = f"\n\n{label} par {interaction.user.name} — *{raison_label}*"
            if chemin_dest:
                suffixe += f" → `{chemin_dest}`"
            else:
                suffixe += " ⚠️ _fichier introuvable_"

            if self.action == "supprimer":
                if interaction.message:
                    try:
                        await interaction.message.delete()
                    except discord.NotFound:
                        # Message déjà supprimé (double-clic, ou vue périmée après restart).
                        log.debug(f"[Renderer] message déjà supprimé pour le clip #{self.clip_num}")
            else:
                await interaction.edit_original_response(
                    content=(interaction.message.content if interaction.message else "") + suffixe,
                    view=None,
                )
            log.info(f"[Renderer] {label} Clip #{self.clip_num} (raison: {raison}) → {chemin_dest}")
        except Exception as exc:
            log.error(f"[Renderer] erreur finalisation raison({self.action}) → {type(exc).__name__}: {exc}")

    async def _revenir_aux_boutons(self, interaction: discord.Interaction) -> None:
        _definir_action_en_attente(self.clip_num, self.channel, None, None)
        clip_view = ClipView(
            channel=self.channel,
            chemin_clip=str(self.chemin_clip) if self.chemin_clip else "",
            clip_num=self.clip_num,
            decision_logger=self.decision_logger,
            mot_repetition=self.mot_repetition,
            structured_logger=self._struct_log,
        )
        if interaction.client:
            interaction.client.add_view(clip_view)
        try:
            await interaction.response.edit_message(view=clip_view)
        except Exception as exc:
            log.error(f"[Renderer] erreur retour aux boutons ({self.action}) → {type(exc).__name__}: {exc}")


# ------------------------------------------------------------------ #
#  Renderer                                                          #
# ------------------------------------------------------------------ #


class Renderer:
    def __init__(self, channel: str, decision_loggers: dict | None = None, struct_log=None) -> None:
        self._client: discord.Client | None = None
        self._channel: discord.TextChannel | None = None
        self._ready = asyncio.Event()
        self._clip_counter: int = 0
        # Un DecisionLogger par channel — voir mainTwitch.py. La review d'un clip doit
        # toujours être journalisée dans le logger de SON channel d'origine, pas celui
        # du premier stream lancé.
        self.decision_loggers: dict = decision_loggers or {}
        self._struct_log = struct_log
        self.channel = channel
        self._pending_task: asyncio.Task | None = None

    def _clip_dir(self, sub: str) -> Path:
        return _CHANNEL(self.channel, sub)

    async def start(self) -> None:
        intents = discord.Intents.default()
        self._client = discord.Client(intents=intents)

        # Pré-enregistrer les views persistantes pour les clips en attente de review
        pending = _lire_pending()
        for entry in pending:
            entry_channel = entry.get("channel", self.channel)
            action_en_attente = entry.get("action_en_attente")
            if action_en_attente:
                # Bouton garder/highlight/supprimer déjà cliqué avant l'arrêt du bot —
                # recharger le menu déroulant de raisons, pas les boutons. Le nom brut du
                # reviewer n'est jamais persisté (RGPD) : seul son hash a survécu.
                view: discord.ui.View = ReasonView(
                    channel=entry_channel,
                    chemin_clip=entry.get("chemin_clip", ""),
                    clip_num=entry["clip_num"],
                    action=action_en_attente,
                    decision_logger=self.decision_loggers.get(entry_channel),
                    mot_repetition=entry.get("mot_repetition"),
                    structured_logger=self._struct_log,
                    reviewer_name=entry.get("reviewer_hash_en_attente") or "unknown",
                    reviewer_is_hash=True,
                    sent_at=entry.get("envoye_at") or time.time(),
                )
            else:
                view = ClipView(
                    channel=entry_channel,
                    chemin_clip=entry.get("chemin_clip", ""),
                    clip_num=entry["clip_num"],
                    decision_logger=self.decision_loggers.get(entry_channel),
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

        self._pending_task = asyncio.create_task(self._verifier_pending())

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

        # Un MERGE (Brain._annuler_dernier_clip remplace un clip déjà entièrement traité
        # par un pic plus fort sous le même clip_num) laissait jusqu'ici un ancien message
        # Discord orphelin : ses boutons restaient actifs, mais la ligne DB de ce clip_num
        # avait déjà été écrasée (ON CONFLICT DO UPDATE) par le nouveau clip — cliquer sur
        # l'ancien message appliquait donc la décision au mauvais clip. On supprime l'ancien
        # message avant d'envoyer le nouveau, tant qu'il en existe un pour ce (channel, clip_num).
        ancien = next(
            (c for c in _lire_pending() if c.get("clip_num") == clip_num and c.get("channel") == streamer),
            None,
        )
        if ancien and ancien.get("message_id"):
            try:
                ancien_msg = await self._channel.fetch_message(ancien["message_id"])
                await ancien_msg.delete()
                log.info(f"[Renderer] 🔀 Ancien message Discord du clip #{clip_num} ({streamer}) supprimé (remplacé par un merge)")
            except discord.NotFound:
                pass
            except Exception as e:
                log.warning(f"[Renderer] ⚠️ Impossible de supprimer l'ancien message mergé #{clip_num} ({streamer}): {e}")

        # Sauvegarder avant envoi pour que les boutons survivent aux redémarrages.
        # Utiliser `streamer` (le channel réel d'origine du clip) et non `self.channel`
        # (fixé au premier channel du bot) — sinon tous les clips de tous les streams
        # se retrouvent classés/déplacés sous le dossier du 1er channel lancé.
        if chemin_hq:
            _ajouter_pending(clip_num, chemin_hq, streamer, mot_rep)

        view = ClipView(
            chemin_clip=chemin_hq or "",
            clip_num=clip_num,
            decision_logger=self.decision_loggers.get(streamer),
            mot_repetition=mot_rep,
            structured_logger=self._struct_log,
            channel=streamer,
        )
        # Enregistrer la view pour que les interactions fonctionnent immédiatement
        if self._client:
            self._client.add_view(view)

        if previews:
            msg = await self._envoyer_avec_previews(contenu, previews, view)
        else:
            msg = await self._channel.send(content=contenu + "\n⚠️ _Pas d'aperçu vidéo_", view=view)

        if msg is not None and chemin_hq:
            _definir_message_id(clip_num, streamer, msg.id)

    async def _envoyer_avec_previews(self, contenu: str, previews: list, view: ClipView) -> discord.Message | None:
        fichiers = []
        if self._channel is None:
            log.error("[Renderer] ❌ _envoyer_avec_previews appelé sans channel")
            return None
        try:
            for p in [Path(p) for p in previews[:10]]:
                if p.exists():
                    taille = p.stat().st_size / 1024 / 1024
                    if taille <= TAILLE_MAX_MB:
                        fichiers.append(discord.File(str(p), filename=p.name))
                    else:
                        log.warning(f"[Renderer] ⚠️ Preview trop lourde ignorée : {p.name} ({taille:.1f} MB)")

            if fichiers:
                msg = await self._channel.send(content=contenu, files=fichiers, view=view)
                log.info(f"[Renderer] ✅ Clip envoyé sur Discord ({len(fichiers)} preview(s))")
            else:
                msg = await self._channel.send(content=contenu + "\n⚠️ _Aperçus trop lourds_", view=view)
            return msg

        except Exception as e:
            log.error(f"[Renderer] ❌ Erreur envoi Discord : {e}")
            try:
                return await self._channel.send(content=contenu + "\n⚠️ _Erreur envoi previews_", view=view)
            except Exception:
                return None
        finally:
            for f in fichiers:
                if hasattr(f, "fp"):
                    f.fp.close()

    # ── Rappels / auto-expiration des reviews en attente ────────────

    async def _verifier_pending(self) -> None:
        while True:
            await asyncio.sleep(VERIF_PENDING_INTERVAL_SEC)
            try:
                await self._traiter_pending_expires()
            except Exception as e:
                log.error(f"[Renderer] ❌ _verifier_pending: {e}")

    async def _traiter_pending_expires(self) -> None:
        if not self._channel:
            return
        maintenant = time.time()
        clips = _lire_pending()
        modifie = False

        for entry in list(clips):
            envoye_at = entry.get("envoye_at")
            # Entrées créées avant l'ajout de ce champ — pas d'horodatage fiable,
            # on ne les fait pas rentrer rétroactivement dans le cycle rappel/expire.
            if envoye_at is None:
                continue

            clip_num = entry["clip_num"]
            channel_clip = entry.get("channel", self.channel)

            # Bouton garder/highlight/supprimer déjà cliqué, mais raison jamais choisie
            # (utilisateur parti sans finir, ou menu périmé) — on ne laisse pas le clip
            # bloqué indéfiniment : on finalise avec la décision déjà prise, sans raison.
            action_en_attente = entry.get("action_en_attente")
            if action_en_attente:
                choisie_at = entry.get("action_choisie_at") or envoye_at
                if maintenant - choisie_at >= RAISON_TIMEOUT_SEC:
                    chemin = Path(entry["chemin_clip"]) if entry.get("chemin_clip") else None
                    chemin_dest = _appliquer_decision(
                        action_en_attente, "non_precisee_auto", channel_clip, chemin, clip_num,
                        self.decision_loggers.get(channel_clip), self._struct_log,
                        entry.get("reviewer_hash_en_attente") or "unknown", True, envoye_at,
                        retirer_du_pending=False,
                    )
                    message_id = entry.get("message_id")
                    label = LABEL_ACTION[action_en_attente]
                    if message_id:
                        try:
                            msg = await self._channel.fetch_message(message_id)
                            if action_en_attente == "supprimer":
                                await msg.delete()
                            else:
                                await msg.edit(
                                    content=msg.content + f"\n\n{label} (raison non précisée après {int(RAISON_TIMEOUT_SEC / 60)} min) → `{chemin_dest}`",
                                    view=None,
                                )
                        except Exception as e:
                            log.debug(f"[Renderer] impossible d'éditer/supprimer le message #{clip_num} (timeout raison): {e}")
                    clips.remove(entry)
                    modifie = True
                    log.warning(f"[Renderer] ⏰ Clip #{clip_num} ({channel_clip}) finalisé automatiquement ({action_en_attente}, raison non précisée)")
                continue

            age = maintenant - envoye_at

            if age >= AUTO_EXPIRE_DELAI_SEC:
                chemin = Path(entry["chemin_clip"]) if entry.get("chemin_clip") else None
                dest = _deplacer_fichier(chemin, channel_clip, "rejected")

                logger_decision = self.decision_loggers.get(channel_clip)
                if logger_decision:
                    logger_decision.log_decision(clip_num, "expire", "auto", reason="timeout_sans_review")
                if self._struct_log:
                    self._struct_log.log_review(
                        clip_num, "expire", "system_auto_expire", 0,
                        reaction_time_sec=round(age, 1), channel=channel_clip,
                        reason="timeout_sans_review",
                        new_file_path=str(dest) if dest else None,
                    )

                message_id = entry.get("message_id")
                if message_id:
                    try:
                        msg = await self._channel.fetch_message(message_id)
                        await msg.edit(
                            content=msg.content + f"\n\n⏰ **Expiré automatiquement** (aucune review après {_format_duree(AUTO_EXPIRE_DELAI_SEC)}) → `{dest}`",
                            view=None,
                        )
                    except Exception as e:
                        log.debug(f"[Renderer] impossible d'éditer le message expiré #{clip_num}: {e}")

                clips.remove(entry)
                modifie = True
                log.warning(f"[Renderer] ⏰ Clip #{clip_num} ({channel_clip}) auto-expiré après {age / 3600:.1f}h sans review")

            elif age >= RAPPEL_DELAI_SEC and not entry.get("rappel_envoye"):
                try:
                    await self._channel.send(
                        f"⏰ Le clip **#{clip_num}** ({channel_clip}) attend une review depuis {int(age // 60)} min."
                    )
                except Exception as e:
                    log.debug(f"[Renderer] rappel Discord échoué pour #{clip_num}: {e}")
                entry["rappel_envoye"] = True
                modifie = True

        if modifie:
            _ecrire_pending(clips)

    async def stop(self) -> None:
        if self._pending_task:
            self._pending_task.cancel()
            try:
                await self._pending_task
            except asyncio.CancelledError:
                pass
        if self._client:
            await self._client.close()
        log.info("[Renderer] 🛑 Bot Discord arrêté")

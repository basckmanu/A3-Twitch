# A3 — Présentation du projet

A3 est un système de détection automatique de moments forts ("clips") sur un ou plusieurs
streams Twitch, à partir du chat en direct. Il tourne en continu à côté du stream, repère les
pics d'activité dans le chat, capture la vidéo correspondante, et soumet chaque clip à une revue
humaine sur Discord avant de le classer.

Aucune analyse vidéo ou audio n'est faite : toute la détection se base sur le texte du chat
Twitch, en temps réel, message par message.

## En un coup d'œil

```
Chat Twitch ──▶ 6 filtres adaptatifs ──▶ Brain (score pondéré) ──▶ StreamCapture (ffmpeg)
                                              │                           │
                                              ▼                           ▼
                                     décision clip/pas clip      clip HQ + aperçu compressé
                                                                          │
                                                                          ▼
                                                                  Discord (revue humaine)
                                                                   Garder / Highlight / Supprimer
                                                                          │
                                                                          ▼
                                                        PostgreSQL (historique, dataset, stats)
```

Un `Watcher`, un `Brain` et un `StreamCapture` tournent en parallèle **par channel surveillé** —
plusieurs streams peuvent être suivis simultanément sans interférence (statistiques, buffers
vidéo et numérotation de clips isolés par channel).

## 1. Détection — les 6 filtres adaptatifs

Chaque message de chat est passé à travers 6 filtres. Cinq d'entre eux (tous sauf
`ClipActivity`) partagent le même mécanisme statistique adaptatif : ils ne se déclenchent pas
sur un seuil fixe, mais sur un **écart significatif par rapport à l'activité récente du chat**
(algorithme de Welford, moyenne/écart-type calculés en ligne sur une fenêtre glissante de 5
minutes). Un chat très actif en continu doit rester "silencieux" pour les filtres tant qu'il ne
dévie pas de sa propre baseline — un chat calme et un chat déchaîné n'ont pas le même seuil
absolu.

| Filtre | Signal mesuré | Détecte |
|---|---|---|
| **MessageRate** | Nombre de messages / 10s | Un pic de vélocité du chat |
| **UniqueAuthors** | Nombre d'auteurs distincts récents (+ "lurkers" qui reprennent la parole) | Un afflux de viewers qui réagissent, pas juste 2-3 personnes qui spamment |
| **Emotions** | Regex sur 6 classes (drôle, rage, hype, choc, tristesse, raid) + emojis | Le ton émotionnel dominant du chat |
| **EmoteDensity** | Ratio d'emotes Twitch/BTTV/FFZ/7TV par message (chargées dynamiquement par channel + cache 1h) | Le spam d'emotes typique d'un moment hype |
| **Repetition** | Mot dominant sur une fenêtre de 10s (hors mots vides / blacklist) | Le chat qui répète tous le même mot ("CLIP CLIP CLIP", "F", etc.) |
| **ClipActivity** | Polling de l'API Twitch (clips créés par les viewers) toutes les 30s | Les viewers qui sont déjà en train de se clipper eux-mêmes le moment |

Chaque filtre adaptatif calibre ses statistiques sur ses ~50 premiers échantillons avant de
pouvoir se déclencher (log `[Watcher] ✅ ... calibré`), puis retourne un **score gradué entre 0
et 1** (0 sous le seuil, 1 à partir du double du seuil en z-score) plutôt qu'un simple oui/non —
un pic à peine au-dessus du seuil pèse moins qu'un pic massif. `ClipActivity` est le seul filtre
binaire (0 ou 1), car il ne réagit pas au chat mais à un signal externe (l'API Twitch).

## 2. Le cerveau (Brain) — de "pic détecté" à "décision de clip"

Le `Brain` agrège les scores des 6 filtres selon des poids fixes et compare le résultat à un
seuil unique :

| Filtre | Poids |
|---|---|
| Emotions | 0.35 |
| ClipActivity | 0.30 |
| MessageRate | 0.20 |
| UniqueAuthors | 0.20 |
| EmoteDensity | 0.20 |
| Repetition | 0.12 |

*(les poids ne somment pas à 1 — ce sont des multiplicateurs indépendants, pas une
distribution)*

**Seuil de déclenchement** : `score_final >= 0.42`.

Quelques mécanismes affinent la décision brute :

- **Mémoire glissante (45s)** : chaque filtre déclenché garde son score pendant 45 secondes,
  pour que plusieurs filtres qui réagissent à quelques secondes d'intervalle (typique d'un vrai
  moment fort) se cumulent dans le score final au lieu d'être évalués isolément message par
  message.
- **Garde-fou volume** : un clip ne peut jamais se déclencher sur la seule base d'un signal
  "qualitatif" (Emotions/EmoteDensity/Repetition) — il faut qu'au moins un filtre de *volume*
  (MessageRate, UniqueAuthors ou ClipActivity) ait aussi réagi, pour éviter qu'un seul message
  très expressif dans un chat quasi vide déclenche un clip.
- **Déduplication (60s)** : un hash (auteur + combinaison de filtres actifs) évite de déclencher
  deux fois sur le même mini-évènement.
- **Fenêtre de merge (150s)** : un nouveau pic dans les 150s suivant un clip déjà validé
  remplace ce clip (si le nouveau pic est au moins aussi fort) plutôt que d'en créer un second —
  évite les doublons sur une hype prolongée.
- **Cooldown adaptatif (120s–480s)** : après un clip, le cooldown avant le prochain est
  proportionnel à l'intensité du score déclenché (un clip énorme "réserve" plus de silence
  ensuite qu'un clip qui a tout juste passé le seuil).
- **Format "TikTok" élastique** : l'enregistrement démarre 45s *avant* le moment détecté
  (rétroactif, grâce au buffer vidéo — voir §3), se prolonge tant que le score reste au-dessus
  du seuil (+15s après le dernier pic), avec une durée minimale de 65s et un plafond de 5 min.

## 3. Capture vidéo (StreamCapture)

`streamlink` capture en continu le flux Twitch (qualité adaptative 480p/360p) et `ffmpeg`
découpe ce flux en segments de 30s dans un **buffer circulaire** (10 min glissantes, purgé au
fur et à mesure). C'est ce qui permet de générer un clip **rétroactif** : au moment où le Brain
décide de clipper, les 45 dernières secondes existent déjà sur disque.

Une fois la fenêtre de clip connue (début/fin), les segments concernés sont concaténés et
découpés avec `ffmpeg` en deux fichiers :
- un clip HQ (qualité de capture) ;
- un ou plusieurs aperçus compressés (`scale=360p`, bitrate réduit) pour respecter la limite
  d'upload Discord (8 Mo).

## 4. Revue humaine sur Discord (Renderer)

Chaque clip généré est posté sur un salon Discord dédié avec un aperçu vidéo et trois boutons :
**✅ Garder**, **⭐ Highlight**, **🗑️ Supprimer**. Cliquer un bouton ouvre un **menu déroulant de
raisons** (ex : *hype authentique*, *jeu compétitif*, *faux positif*, *doublon*...) — un choix
structuré plutôt qu'un champ libre, pour que les raisons de garder/rejeter un clip soient
exploitables statistiquement plus tard.

Le fichier vidéo est déplacé physiquement selon la décision
(`clips/{channel}/{validated,highlights,rejected}`), et la décision (+raison, +latence de
réaction du reviewer) est journalisée à la fois dans un fichier JSON de session
(`decisions/{channel}/`) et en base PostgreSQL.

Robustesse de la queue de revue :
- **Persistance** (`pending_reviews.json`) — les clips en attente de review survivent à un
  redémarrage du bot, boutons compris.
- **Rappel** après 30 min sans décision.
- **Auto-expiration** après 24h sans décision (classé "rejeté", raison `timeout_sans_review`).
- Si un reviewer clique un bouton mais ne choisit jamais de raison, le clip se finalise quand
  même après 15 min (raison `non_precisee_auto`) plutôt que de rester bloqué indéfiniment.
- Accès aux boutons restreignable à une liste d'IDs Discord (`DISCORD_ALLOWED_USERS`).

Un **bilan de session** (durée, nombre de clips, score moyen/max, distribution des scores,
filtres les plus actifs) est posté sur Discord à l'arrêt du bot.

## 5. Données & vie privée

Tous les identifiants utilisateurs (pseudo Twitch de l'auteur qui déclenche un clip, reviewer
Discord) sont **pseudonymisés** avant tout stockage : hash SHA-256 tronqué (16 caractères),
salé avec un secret propre à chaque déploiement (`A3_HASH_SALT`) — jamais de pseudo en clair en
base ni dans les logs structurés.

Au-delà des décisions de review, A3 journalise en continu un **dataset structuré** en base
PostgreSQL, pensé pour l'analyse et un futur réentraînement des poids/seuils :
- `filter_events` : chaque déclenchement de filtre (partitionné par mois) ;
- `chat_windows` : fenêtres de 60s agrégées (débit de messages, densité d'emotes, score
  émotionnel moyen...) — avec ou sans clip déclenché, pour comparer moments calmes/hype ;
- `snapshots` : état périodique (toutes les 5 min) du chat et des filtres, pour du monitoring ;
- `clips` / `reviews` / `sessions` : cycle de vie complet de chaque clip et de chaque session de
  stream, avec métadonnées Twitch (viewer count, catégorie de jeu, langue) capturées en continu
  via un poller dédié.

## 6. Déploiement & fiabilité

- **Docker** (`docker/docker-compose.yml`) — volumes persistants pour les clips reviewés, les
  décisions, les logs et la file de review Discord ; `restart: unless-stopped` pour un
  redémarrage automatique en cas de crash.
- **PostgreSQL** — seul moteur supporté ; schéma canonique versionné dans
  `sql/schema_postgresql.sql`.
- **CI GitHub Actions** — `ruff`, `mypy`, `pytest` à chaque push/PR.
- **Politique de rétention disque** : buffer vidéo et clips non reviewés purgés après 2 jours ;
  clips explicitement gardés/highlightés conservés 30 jours (jamais la même politique que les
  clips rejetés — une décision humaine de conservation mérite plus que 2 jours).
- **Calibration adaptative par channel** — chaque stream surveillé a ses propres statistiques de
  filtres (pas de baseline partagée entre deux chats très différents en volume).

## Ce qu'A3 ne fait pas (aujourd'hui)

- Pas d'analyse du flux vidéo/audio — la détection est 100% basée sur le texte du chat.
- Pas de ré-ajustement automatique des poids/seuil — le volume de clips reviewés est encore
  trop faible pour entraîner quoi que ce soit sans surapprentissage ; le dataset structuré existe
  en prévision de ça.
- Pas de dashboard d'analyse encore branché sur les tables agrégées (`chat_windows`,
  `snapshots`, `filter_events`) — les données sont collectées, l'exploitation reste à construire.

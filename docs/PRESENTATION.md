# A3 (Peakr) — Présentation du projet

> Document de référence pour un collaborateur ou partenaire technique qui rejoint le projet
> sans avoir lu le code. Toutes les valeurs citées (seuils, poids, délais, tailles) sont reprises
> telles quelles du code source — aucune n'est estimée ou arrondie de tête.

## En une phrase

A3 est un bot qui surveille en continu le chat Twitch d'un ou plusieurs streamers, détecte
automatiquement les moments de hype à partir du **texte du chat uniquement** (aucune analyse
vidéo ou audio) via six filtres statistiques adaptatifs, capture rétroactivement le clip vidéo
correspondant, et le soumet à une revue humaine sur Discord avant classement.

## Pipeline de bout en bout

```
Chat Twitch (par message)
      │
      ▼
Watcher ── passe le message à 6 filtres adaptatifs (Welford, par channel)
      │      MessageRate · UniqueAuthors · Emotions · EmoteDensity · Repetition · ClipActivity
      │      → chaque filtre retourne un score gradué 0.0–1.0
      ▼
Brain ── agrège les scores pondérés (mémoire glissante 45s) et compare au seuil
      │      score_final >= 0.42 ET au moins un filtre "volume" actif → clip
      │      merge / dedup / cooldown adaptatif filtrent les faux doublons
      ▼
StreamCapture ── ffmpeg découpe le clip depuis un buffer vidéo circulaire (rétroactif)
      │      → 1 clip HQ + 1 aperçu compressé (limite Discord 8 Mo)
      ▼
Renderer ── poste le clip sur Discord avec 3 boutons de revue humaine
      │      ✅ Garder · ⭐ Highlight · 🗑️ Supprimer (+ raison sélectionnée dans un menu)
      ▼
PostgreSQL ── historique complet (sessions, clips, reviews, fenêtres de chat, snapshots)
             pseudonymisé — pensé comme dataset pour un futur réentraînement des poids
```

Un `Watcher`, un `Brain`, un `StreamCapture` et un `DecisionLogger` tournent **par channel
surveillé** (statistiques Welford, buffer vidéo et numérotation de clips isolés channel par
channel) ; un seul `Renderer` (bot Discord) et un seul `StructuredLogger`/connexion PostgreSQL
sont partagés entre tous les channels. Les channels surveillés sont définis par la variable
d'environnement `CHANNELS` (actuellement configurée en prod avec 3 channels, ex. `jltomy,
anyme023, inoxtag`).

---

## 1. Détection — les 6 filtres adaptatifs (`Watcher`)

Chaque message de chat est passé à travers 6 filtres, un par instance de channel. Cinq d'entre
eux héritent de la même classe de base `FiltreAdaptatif`
(`watcherFiltreBase.py`) et partagent un mécanisme commun ; `ClipActivity` est indépendant car il
ne réagit pas au chat mais à un polling externe.

### Le mécanisme commun : seuil adaptatif à double fenêtre de Welford

Plutôt qu'un seuil fixe (« plus de X messages/10s = pic »), chaque filtre calcule sa propre
moyenne et son écart-type **en ligne** (algorithme de Welford, sans reparcourir l'historique à
chaque message) sur deux fenêtres glissantes simultanées :

- une fenêtre courte : `fenetre_welford = 300s` (5 min) — la baseline « récente » ;
- une fenêtre longue : `fenetre_fond = fenetre_welford * 4 = 1200s` (20 min) — le « fond » de
  l'activité du chat.

Un pic est retenu seulement si les **deux** conditions suivantes sont vraies :

1. `z_score` local ≥ `1.8` par rapport à la fenêtre courte (le signal dévie significativement de
   la moyenne récente) ;
2. le signal brut dépasse `ratio_fond_min = 1.3` fois la moyenne de la fenêtre longue.

Le pic doit ensuite tenir au moins `duree_min_pic = 1.5s`, et un filtre ne peut se redéclencher
que si `cooldown = 45s` se sont écoulées depuis son dernier déclenchement. Un filtre calibre ses
statistiques sur ses **50 premiers échantillons** (`min_samples = 50`) avant de pouvoir se
déclencher — un filtre qui n'a pas encore vu assez de données renvoie systématiquement 0.

**Pourquoi une double fenêtre plutôt qu'une seule ?** Une seule fenêtre glissante absorbe
progressivement un pic d'activité durable (par exemple un raid Twitch qui fait exploser le débit
de messages) comme sa nouvelle normalité — au bout de quelques minutes, le chat déchaîné devient
la baseline, et plus rien ne semble être un pic. La fenêtre longue sert de garde-fou : même si la
fenêtre courte s'est laissée « polluer » par un raid, le signal doit rester significativement
au-dessus du fond de 20 minutes pour compter comme un vrai pic.

Chaque filtre retourne un **score gradué entre 0.0 et 1.0** plutôt qu'un booléen : 0 sous le
seuil, et une intensité proportionnelle à l'écart au-dessus du seuil z, plafonnée à 1.0 au double
du seuil (`intensite = (z_actuel - z_score) / z_score`, bornée à [0, 1]). Un pic à peine
au-dessus du seuil pèse donc moins qu'un pic massif dans le score final calculé par le `Brain`.

### Les 6 filtres

| Filtre | Signal mesuré | Paramètres propres | Détecte |
|---|---|---|---|
| **MessageRate** | Nombre de messages sur une fenêtre glissante de `10s` | — | Un pic de vélocité brute du chat |
| **UniqueAuthors** | Nombre d'auteurs distincts sur `10s`, + auteurs « lurkers » qui reprennent la parole après une longue absence | `quota_spam = 3` : au-delà de 3 messages du même auteur en 10s, ses messages suivants ne comptent plus dans le signal (anti-spam) | Un afflux réel de viewers qui réagissent, pas 2-3 personnes qui spamment |
| **Emotions** | Proportion de mots matchant l'une des 6 classes regex (`drole`, `rage`, `hype`, `choc`, `tristesse`, `raid`) + emojis | ~40 patterns regex répartis sur les 6 classes | Le ton émotionnel dominant du chat |
| **EmoteDensity** | Ratio emotes/mots par message, moyenné sur `10s`, avec un seuil plancher `seuil_absolu = 0.08` (sous ce ratio, le filtre ne calcule même pas de score) | Emotes chargées dynamiquement (Twitch/BTTV/FFZ/7TV, globales + spécifiques au channel), cache disque `1h` (`3600s`) | Le spam d'emotes typique d'un moment hype |
| **Repetition** | Fréquence du mot le plus répété sur une fenêtre de `10s` (hors mots vides et blacklist) | `longueur_min_mot = 2` | Le chat qui répète tous le même mot (« CLIP CLIP CLIP », « F »...) |
| **ClipActivity** | Polling de l'API Twitch Helix (`/helix/clips`) toutes les `30s` | `seuil_clips = 1` clip dans une fenêtre de `90s` → score `1.0` ; `cooldown = 60s` | Les viewers qui sont déjà en train de se clipper eux-mêmes le moment — signal externe, pas dérivé du chat |

`ClipActivity` est binaire (0.0 ou 1.0) et n'hérite pas de `FiltreAdaptatif` : il n'a pas de
notion de baseline à calibrer, c'est un simple compteur de clips récents via l'API Twitch.

Le `Watcher` journalise aussi, indépendamment de la détection de clip, deux flux de données
agrégées :
- des **fenêtres de chat** de `60s` (`FENETRE_CHAT_SEC`), pensées comme dataset pour un futur
  entraînement supervisé (features de fenêtre → label de review une fois le clip traité) ;
- des **snapshots** d'état toutes les `300s` (`INTERVALLE_SNAPSHOT_SEC`), pour un futur dashboard
  de monitoring — cadence volontairement plus large que les fenêtres de chat, ce n'est pas le même
  usage.

---

## 2. Le cerveau — de « pic détecté » à « décision de clip » (`Brain`)

Le `Brain` (`mainBrainTwitch.py`) reçoit les scores des 6 filtres à chaque message et décide s'il
faut déclencher un clip.

### Score pondéré

```python
POIDS_FILTRES = {
    "FiltreMessageRate":   0.20,
    "FiltreUniqueAuthors": 0.20,
    "FiltreEmotions":      0.35,
    "FiltreEmoteDensity":  0.20,
    "FiltreRepetition":    0.12,
    "FiltreClipActivity":  0.30,
}
SEUIL_CLIP = 0.42
```

Ces poids **ne somment pas à 1** (total 1.37) — ce sont des multiplicateurs indépendants
appliqués à des scores qui peuvent se cumuler (plusieurs filtres actifs en même temps), pas une
distribution de probabilité. Un clip se déclenche quand `score_final >= 0.42`.

### Mémoire glissante de 45 secondes

Les filtres ne réagissent pas tous à la même milliseconde sur un vrai moment fort (le pic de
messages précède souvent de quelques secondes le pic d'emotes, par exemple). Le `Brain` retient
donc le score de chaque filtre pendant `fenetre_memoire_sec = 45s` après son dernier
déclenchement, et recalcule le score final à chaque message en sommant tous les filtres encore
« actifs » dans cette fenêtre — plutôt que d'évaluer chaque message isolément, ce qui raterait des
signaux qui se répondent à quelques secondes d'intervalle.

*(Note : un utilitaire séparé, `test_filtres_live.py` — un dashboard de test en conditions réelles,
pas un test automatisé — implémente une classe `FenetreCoincidence` avec une fenêtre de 25s pour la
même idée. Ce n'est pas le mécanisme utilisé en production : le `Brain` réel utilise la mémoire
glissante de 45s décrite ci-dessus.)*

### Garde-fou « volume »

```python
FILTRES_VOLUME = {"FiltreMessageRate", "FiltreUniqueAuthors", "FiltreClipActivity"}
```

Un clip ne peut jamais se déclencher sur la seule base d'un signal qualitatif (Emotions,
EmoteDensity, Repetition) : il faut qu'au moins un filtre de volume ait aussi réagi. Un seul
message très expressif isolé dans un chat par ailleurs calme ne suffit donc pas.

### Déduplication (60s)

Un hash MD5 tronqué (auteur + combinaison triée des filtres actifs) est retenu pendant
`_fenetre_dedup_sec = 60s`. Si le même hash réapparaît dans cette fenêtre, le déclenchement est
rejeté comme doublon du même mini-évènement.

### Fenêtre de merge (150s) et cooldown adaptatif

```python
MERGE_WINDOW_SEC = 150
COOLDOWN_MIN_SEC = 120
COOLDOWN_MAX_SEC = 480
```

Si un nouveau pic survient dans les 150s suivant le clip précédent : s'il est au moins aussi fort
(`score_final >= score_dernier_clip`), il **remplace** le clip précédent (le clip Discord déjà
envoyé est retiré de l'historique interne, mais son message Discord existant est supprimé et
remplacé — voir `_annuler_dernier_clip` et la logique de merge du `Renderer`) plutôt que de créer
un doublon. Sinon, il est rejeté.

Passé la fenêtre de merge, un nouveau clip doit attendre un **cooldown adaptatif** entre
`120s` et `480s`, calculé proportionnellement à l'intensité du score qui a déclenché le clip
précédent :

```python
ratio = min((score - seuil) / (1.0 - seuil), 1.0)
cooldown = COOLDOWN_MIN_SEC + ratio * (COOLDOWN_MAX_SEC - COOLDOWN_MIN_SEC)
```

**Pourquoi adaptatif plutôt que fixe ?** Un clip qui a tout juste dépassé le seuil (0.42) ne
« consomme » que 120s de silence avant le prochain ; un clip énorme (score proche de 1.0) réserve
jusqu'à 480s, sur l'hypothèse qu'un moment aussi fort est suivi d'une retombée progressive du
chat qu'il ne sert à rien de re-clipper immédiatement.

### Format vidéo « élastique » façon TikTok

```python
DECALAGE_RECORD_AVANT_SEC = 45.0   # début du clip = 45s AVANT le déclenchement
DUREE_ATTENTE_HYPE_SEC    = 15.0   # prolongation après chaque nouveau pic pendant l'enregistrement
DUREE_MIN_TIKTOK_SEC      = 65.0   # durée plancher du clip final
```

Grâce au buffer vidéo circulaire (voir §3), l'enregistrement d'un clip démarre **rétroactivement**
45 secondes avant l'instant du déclenchement — le moment qui a fait grimper le score est donc
inclus dans le clip, pas seulement ce qui suit. Tant que le score reste au-dessus du seuil,
l'heure de fin prévue recule de 15s à chaque nouveau pic (jusqu'à un plafond dur de 5 minutes
d'enregistrement, forcé pour ne pas dépasser le buffer). Si la durée finale calculée est
inférieure à 65s, le début du clip est reculé pour l'atteindre — une contrainte de format pensée
pour la republication sur des plateformes type TikTok/Shorts qui pénalisent les vidéos trop
courtes.

---

## 3. Capture vidéo (`StreamCapture`)

```python
BUFFER_DUREE_MAX_SEC = 600   # 10 min de buffer glissant
DUREE_SEGMENT_SEC    = 30
DELAI_CHAT_VIDEO_SEC = 8
QUALITE_STREAM = "480p,480p30,360p,360p30"
```

`streamlink` capture en continu le flux Twitch (qualité 480p en priorité, avec repli en 360p), et
`ffmpeg` (`-c copy`, sans réencodage) découpe ce flux en segments de 30s dans un dossier
`buffer_segments/{channel}/`. Un buffer circulaire en mémoire (`deque`) référence les 10 dernières
minutes de segments ; les plus anciens sont supprimés du disque au fur et à mesure. C'est ce
tampon qui permet le clip **rétroactif** décrit au §2 : au moment où le `Brain` décide de clipper,
les 45 dernières secondes existent déjà physiquement sur disque.

`DELAI_CHAT_VIDEO_SEC = 8` compense le décalage naturel entre l'arrivée d'un message de chat et
l'affichage de l'instant correspondant dans le flux vidéo (latence de diffusion Twitch) : les
timestamps de découpe sont décalés de 8s en arrière pour que le clip vidéo corresponde réellement
à ce qui se passait à l'écran au moment du message qui a déclenché la détection.

Au découpage, deux fichiers sont générés en parallèle avec `ffmpeg` :
- un clip HQ (copie directe des segments, sans perte) ;
- un aperçu compressé (`scale=-2:360`, H.264 `crf 32`, bitrate vidéo plafonné à `600k`, audio
  AAC `64k`) — dimensionné pour respecter la limite d'upload de fichier Discord de **8 Mo**
  (`TAILLE_MAX_MB = 8.0` côté `Renderer`).

---

## 4. Revue humaine sur Discord (`Renderer`)

Chaque clip généré est posté avec l'aperçu vidéo et trois boutons : **✅ Garder**,
**⭐ Highlight**, **🗑️ Supprimer**. Cliquer un bouton n'applique pas immédiatement la décision : il
ouvre un **menu déroulant de raisons** (ex. côté validation : *hype authentique*, *jeu
compétitif*, *drôle/troll*, *fail*, *interaction chat forte* ; côté rejet : *faux positif*, *hype
réelle mais pas assez forte*, *doublon*, *problème technique*, *contenu sensible*). Un menu plutôt
qu'un champ libre : un clic de plus, mais des valeurs stables et agrégeables en SQL au lieu de
texte libre inexploitable pour l'analyse.

Une fois la décision et la raison connues, le fichier vidéo est **déplacé physiquement** vers
`clips/{channel}/{validated|highlights|rejected}/`, et la décision (+ raison + latence de réaction
du reviewer) est journalisée à la fois dans `decisions/{channel}/session_*.json` et en base
PostgreSQL.

Robustesse de la file de revue (`pending_reviews.json`) :

- **Persistance entre redémarrages** — les clips en attente de review et leurs boutons Discord
  survivent à un redémarrage du bot (les vues Discord sont ré-enregistrées au démarrage à partir
  du fichier `pending_reviews.json`).
- **Rappel** après `RAPPEL_DELAI_SEC = 1800s` (30 min) sans décision — un message Discord signale
  le clip en attente.
- **Auto-expiration** après `AUTO_EXPIRE_DELAI_SEC = 86400s` (24h) sans aucune décision : le clip
  est classé « rejeté » avec la raison `timeout_sans_review`.
- **Timeout de raison** : si un bouton (garder/highlight/supprimer) est cliqué mais qu'aucune
  raison n'est jamais choisie dans le menu déroulant (utilisateur parti, menu périmé), la décision
  se finalise quand même après `RAISON_TIMEOUT_SEC = 900s` (15 min) avec la raison
  `non_precisee_auto`, plutôt que de rester bloquée indéfiniment.
- **Accès restreignable** : `DISCORD_ALLOWED_USERS` (liste d'IDs Discord) limite qui peut cliquer
  les boutons — si la variable est vide, n'importe qui a accès au salon peut décider (le code logue
  un avertissement explicite à ce sujet au démarrage).
- **Gestion des merges** : quand le `Brain` remplace un clip par un pic plus fort sous le même
  numéro (voir §2), l'ancien message Discord est supprimé avant l'envoi du nouveau, pour éviter
  que ses boutons pointent encore vers un clip déjà écrasé en base.

Un **bilan de session** (durée, nombre de clips, taux de validation, score moyen/max, distribution
des scores par tranche, filtres les plus actifs, répartition par heure) est posté sur Discord à
l'arrêt du bot, calculé à partir de l'historique des clips effectivement terminés (pas du simple
compteur incrémenté à la détection, pour éviter un clip encore en cours de traitement au moment de
l'arrêt).

---

## 5. Données & vie privée

Tous les identifiants utilisateurs (pseudo Twitch de l'auteur qui déclenche un clip, pseudo du
mot répété, reviewer Discord) sont **pseudonymisés avant tout stockage**, en base comme dans les
logs structurés :

```python
hashlib.sha256(f"{A3_HASH_SALT}:{value}".encode()).hexdigest()[:16]
```

Un hash SHA-256 tronqué à 16 caractères, salé avec `A3_HASH_SALT` — une valeur secrète propre à
chaque déploiement, jamais commitée (le `.env.example` insiste sur le fait qu'un salt connu
permettrait de reconstruire une rainbow table sur l'espace des pseudos Twitch et de désanonymiser
tout le monde). La fonction lève une erreur explicite si `A3_HASH_SALT` n'est pas défini plutôt
que de silencieusement stocker en clair. Les identifiants Discord bruts (`user_id`) ne sont
**jamais** stockés en base : `log_review` force `user_id: 0` en dur, seul le hash du pseudo est
conservé.

Au-delà des décisions de review, A3 journalise en continu un **dataset structuré** en base
PostgreSQL, explicitement pensé comme matière première pour un futur réajustement des poids/seuils
(pas encore exploité automatiquement — voir §7) :

- `filter_events` : chaque déclenchement de filtre, table partitionnée par mois ;
- `chat_windows` : fenêtres de 60s agrégées (débit de messages, densité d'emotes, score émotionnel
  moyen...), avec ou sans clip déclenché — pour pouvoir comparer moments calmes et moments de hype ;
- `snapshots` : état périodique (toutes les 5 min) du chat et des filtres ;
- `clips` / `reviews` / `sessions` : cycle de vie complet de chaque clip et de chaque session de
  stream, avec métadonnées Twitch (viewer count, catégorie de jeu, langue) capturées en continu par
  un poller dédié (`StreamMetadataPoller`, appel `/helix/streams` toutes les 60s).

---

## 6. Déploiement & fiabilité

- **Docker** (`docker/docker-compose.yml`) : conteneur unique `network_mode: host` (nécessaire
  pour que `streamlink` fonctionne correctement), `restart: unless-stopped`. Volumes persistants
  séparés pour les clips en attente (`clips_output`), les clips reviewés (`clips`), les décisions
  JSON, les logs, le cache d'emotes, et `pending_reviews.json` (un fichier, pas un dossier — le
  Dockerfile le pré-crée avant montage, sinon Docker en ferait un dossier).
- **PostgreSQL** : seul moteur supporté (`DB_TYPE=postgres` ; toute autre valeur bascule sur un
  `DummyDBHandler` qui n'écrit rien). `PostgresHandler` crée et migre automatiquement son propre
  schéma à la connexion (tables, index, partitions mensuelles glissantes sur 3 mois pour
  `stream_events`/`filter_events`, renommages de colonnes hérités d'anciens schémas) — le code SQL
  fait foi. Le README mentionne un fichier `sql/schema_postgresql.sql` comme schéma canonique
  exportable via `pg_dump`, mais ce fichier n'est **pas présent** dans cette copie du dépôt.
- **Écriture asynchrone en base** : les events sont mis en file (`queue.Queue`, taille max 20000)
  et insérés par lots (`batch_size = 100`, ou toutes les `5s`) par un thread worker dédié, pour ne
  jamais bloquer la boucle de traitement du chat sur une latence réseau vers PostgreSQL. Chaque
  insertion est isolée dans un `SAVEPOINT` pour qu'un échec sur une table secondaire
  (`filter_events`, `stream_events`...) n'invalide pas toute la transaction de l'event principal.
- **Politique de rétention disque**, appliquée toutes les heures (`CLEANUP_INTERVAL_SEC = 3600`) :
  - clips jamais reviewés (`clips_output/`) et clips explicitement rejetés (`clips/*/rejected`) :
    purgés après `RETENTION_DAYS = 2` jours ;
  - clips explicitement gardés/highlightés (`clips/*/validated`, `clips/*/highlights`) : conservés
    plus longtemps, `KEPT_RETENTION_DAYS = 30` jours (pas de conservation illimitée, mais un délai
    x15 plus long qu'un clip rejeté).

  *Pourquoi cette distinction ?* Un commentaire dans `decisions.py` documente un incident réel :
  appliquer la même rétention courte (2 jours) aux clips gardés/highlightés qu'aux clips rejetés a
  entraîné la suppression définitive des 5 seuls clips « garder »/« highlight » alors en base, dès
  qu'ils ont dépassé la fenêtre de 2 jours — alors qu'un reviewer humain avait explicitement choisi
  de les conserver. `KEPT_RETENTION_DAYS` corrige ce comportement tout en bornant quand même la
  durée de vie (pas de croissance disque non bornée).
  - le buffer vidéo brut (`buffer_segments/`) est également purgé après 2 jours, en balayant tous
    les channels présents sur disque (pas seulement ceux actuellement dans `CHANNELS`) — un channel
    retiré de la config n'a plus de `DecisionLogger` actif pour nettoyer ses propres fichiers,
    donc ce balayage global évite une fuite disque orpheline.
- **Sessions orphelines** : si le process est tué sans passer par l'arrêt propre (`close()`), sa
  session reste `active` en base indéfiniment. Au démarrage, `PostgresHandler` referme toute
  session `active` de plus de 12h (`ORPHAN_SESSION_AGE_SEC`) comme `interrupted` — un seuil large
  pour ne jamais couper une session réellement en cours (au-delà de la durée typique d'un stream
  Twitch).
- **CI GitHub Actions** (`.github/workflows/ci.yml`) : à chaque push sur `main` et chaque pull
  request, `ruff check src/ tests/`, `mypy src/`, puis `pytest tests/ -v`.

---

## 7. Ce que le projet ne fait pas (encore)

- **Aucune analyse vidéo ou audio** : la détection repose à 100 % sur le texte du chat Twitch. Un
  moment visuellement ou sonorement fort mais silencieux dans le chat (peu de spectateurs actifs,
  ou spectateurs qui regardent sans écrire) ne sera pas détecté.
- **Pas de tests automatisés réels branchés sur la CI.** `pyproject.toml` déclare
  `testpaths = ["tests"]` et la CI exécute `pytest tests/ -v`, mais **aucun dossier `tests/` à la
  racine du dépôt n'existe** dans cette copie. Le seul fichier apparenté à des tests,
  `src/a3/Twitch/tests/test_filtres_live.py`, est en réalité un **dashboard de calibration en
  conditions réelles** (il se connecte à un vrai channel Twitch et affiche l'état des filtres en
  direct) — ce n'est pas une suite de tests unitaires. En l'état, l'étape `pytest` de la CI est
  vraisemblablement en échec ou ne collecte aucun test ; il n'existe pas de couverture automatisée
  de la logique de scoring, du cooldown, ou de la déduplication.
- **Pas de ré-ajustement automatique des poids ou du seuil.** Les tables `filter_performance` et
  `reviews` accumulent le signal nécessaire (quel filtre a contribué à quel clip, gardé ou
  rejeté), mais rien ne les exploite aujourd'hui pour recalibrer `POIDS_FILTRES` ou `SEUIL_CLIP` —
  le dataset existe en prévision de ça, pas encore le mécanisme de réentraînement.
- **Pas de dashboard d'analyse** branché sur les tables agrégées (`chat_windows`, `snapshots`,
  `filter_events`, `filter_performance`) : les données sont collectées en continu, mais leur
  exploitation (visualisation, alerting, comparaison de channels) reste à construire.
- **Schéma SQL canonique absent du dépôt.** Le README documente un fichier
  `sql/schema_postgresql.sql` régénérable via `pg_dump`, mais ce fichier n'est pas présent dans
  cette copie — le schéma de référence n'existe aujourd'hui que dans le code de création de tables
  de `PostgresHandler`, pas comme artefact versionné indépendant.
- **Un seul moteur de base de données supporté** (PostgreSQL) : toute autre valeur de `DB_TYPE`
  fait tourner le bot sans aucune persistance en base (silencieusement, via `DummyDBHandler`),
  seuls les fichiers JSON locaux (`decisions/`, `logs/structured/`) restent alimentés.
- **Pas de multi-tenant réel côté DB.** Le schéma prévoit une table `organizations` et un
  `org_id` sur plusieurs tables, mais tout le déploiement actuel tourne sur une organisation
  unique (`'default'`) créée automatiquement — la structure existe en vue d'une éventuelle offre
  multi-clients (SaaS), mais rien n'isole aujourd'hui plusieurs organisations entre elles.

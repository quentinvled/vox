# Vox — assistant audio global (idées, non implémenté)

Statut : **conception validée, implémentation en cours.** L'avancement réel est
suivi dans [`roadmap.md`](roadmap.md). Voir aussi
[`modeles-diarisation.md`](modeles-diarisation.md) pour la partie modèles et
coûts.

## 1. Vision

Vox passe de « dictée vocale » à **assistant audio global**, en trois couches :

| Couche | Contenu |
|---|---|
| **Enregistrer** | Micro, son du système, ou les deux. Notes vocales nommées, appels, imports de fichiers. |
| **Traiter** | Transcrire, diariser, corriger, renommer, exporter, réécouter. |
| **Assister** | Transcription live pendant un appel, puis agent « qu'est-ce que je réponds ? ». |

## 2. Principes de conception

1. **Windows d'abord.** Linux ensuite. macOS hors périmètre (nécessiterait
   ScreenCaptureKit/BlackHole pour le son système).
2. **Zéro contrainte technique imposée.** L'utilisateur ne choisit ni modèle, ni
   fournisseur, ni « il faut deux pistes pour bien diariser ». Vox analyse ce
   qu'on lui donne et s'adapte tout seul. Les seuls choix visibles sont
   éditoriaux : nom du fichier, prénoms des locuteurs, export.
3. **L'UI/UX actuelle est la référence.** Un seul nouvel écran (la Bibliothèque,
   évolution de la fenêtre d'historique) ; tout le reste réutilise les
   composants existants.

## 3. Le routeur automatique

À l'import ou à l'arrêt d'un enregistrement, Vox analyse le fichier et décide
seul :

| Source détectée | Stratégie appliquée (sans rien demander) |
|---|---|
| Mono, une seule voix (vocal WhatsApp) | Transcription simple, pas de diarisation → coût minimal |
| Mono, plusieurs voix | Diarisation API automatique (plafond de locuteurs généreux) |
| Stéréo, canaux identiques (corrélation ≈ 1) | Traité comme mono |
| Stéréo, canaux différents | Séparation par canal (« Moi » / « Interlocuteur »), diarisation du canal système seulement s'il contient plusieurs voix |
| Plusieurs fichiers (micro + système) | Même logique appliquée d'office |
| Fichier > 30 min | Découpage invisible aux silences, fusion automatique |
| Modèle en échec (timeout, 503, pas de diarisation) | Repli automatique sur le suivant ; ligne discrète dans les détails |

Si la diarisation reste imparfaite, on ne bloque pas : le transcript arrive avec
des **outils de correction** (fusionner deux locuteurs, réassigner un segment,
renommer). C'est ce qui rend l'outil « libre ».

## 4. Enregistrement multi-sources

| Brique | Solution Windows | Remarque |
|---|---|---|
| Micro | `sounddevice` (déjà dans le projet) | Rien à changer |
| Son du système | WASAPI loopback via `PyAudioWPatch` | Repli : `soundcard` |
| Les deux | 2 flux → 2 fichiers séparés | Mixage seulement à l'export |
| Vidéo d'écran | ffmpeg (`ddagrab` / `x11grab`) | Chantier séparé, plus tard |

Décisions :

- **Toujours enregistrer les pistes séparément** quand il y en a plusieurs : la
  piste micro = moi, la piste système = les interlocuteurs. Diarisation gratuite
  et parfaite pour « moi », et clustering plus propre sur le canal système.
- **Fusion à l'écoute uniquement** (bouton « écouter la version mixée » ou à
  l'export), pas au stockage.
- **Dérive d'horloge** : deux appareils, deux horloges → quelques ms/min d'écart.
  Resampling/alignement automatique au mixage. Sans impact sur la transcription
  (pistes traitées séparément).

### Pièges à gérer proprement

- **Bluetooth** : activer le micro fait basculer le casque en profil HFP → le son
  système tombe en 8-16 kHz. L'afficher dans l'UI (« qualité son système
  dégradée »), ne pas bloquer.
- **Mode exclusif WASAPI** : certaines apps prennent la sortie en exclusif →
  loopback muet. Le détecter et prévenir.
- **Périphérique débranché en cours d'enregistrement** : continuer l'autre piste,
  notification discrète.

**Implémenté le 01/10/2026 (détection)** : Vox repère à la demande le micro
(réglages ou défaut), la sortie son du système (WASAPI loopback via
`PyAudioWPatch`) et les applications qui jouent du son (`pycaw`). Les
indicateurs sont dans la zone de notification — pastille verte / orange / rouge
sur l'icône, détail dans le menu « Entrées audio », action « Tester les
entrées… » — et la détection n'ouvre aucun flux. La capture réelle (deux
pistes séparées) est l'étape suivante.

**Implémenté le 02/10/2026 (enregistrement)** : le test des entrées tourne en
continu (toutes les 5 s, instance PyAudio réutilisée) et les lignes du menu
sont colorées et cliquables. Un clic sur « Enregistrer un appel (micro + son
système)… » démarre les deux pistes ; la pilule montre chrono, niveaux et un
bouton d'arrêt (masquable, l'icône passe au rouge). À l'arrêt : fichiers séparés
dans `appels/`, transcription automatique de chaque piste (micro sans
diarisation, système diarisé), recollage temporel (`calls.merge_tracks`) et
rangement dans la bibliothèque. Restent : mixage à l'écoute, modes
micro/système/les deux explicites, pause, et les pièges Bluetooth.

## 5. Détection des appels

Principe : ce n'est pas de la magie, c'est **« quelle app tient le micro, et
depuis combien de temps »**.

- **Windows** : sessions audio via `pycaw` (le process qui capture), `psutil`
  pour le nom du process, `EnumWindows` (ctypes) pour le titre de fenêtre.
- Heuristique : app en **allowlist** (WhatsApp, Teams, Zoom, Discord, Slack,
  Signal, Telegram) + micro ouvert > 3 s (+ sortie audio active) → **popup non
  bloquant** « Appel WhatsApp détecté — Enregistrer micro + système ? ».
- Trois boutons : `Enregistrer`, `Plus tard`, `Ne plus demander pour WhatsApp`.
- Un mode « confiance » (enregistrement direct pour certaines apps) est
  envisageable en réglage, mais le défaut est de demander.
- Un bouton manuel **« Enregistrer un appel »** existe toujours dans le menu.
- Fiabilité réaliste ~90 % : certaines apps gardent le micro ouvert. C'est pour
  ça que le popup reste une suggestion.

## 6. Bibliothèque

La fenêtre d'historique existante évolue (mêmes widgets, mêmes boutons) :

- Filtres en `Chip` : Tout / Dictées / Notes / Appels / Imports.
- Recherche plein texte.
- Détail : lecteur (`QMediaPlayer`, déjà en place) + **transcript cliquable**
  (chaque segment avec timestamp → seek), pastilles de couleur par locuteur,
  renommage inline, bouton « Transcrire / Retranscrire », coût et modèle utilisé.
- Nom automatique proposé : `Appel WhatsApp — 29/09 14:32` (modifiable).
- Bouton « Proposer les prénoms » (LLM), prénoms mémorisés par contact.

**Implémenté le 30/09/2026** (étapes 1.8 et 1.9) : les filtres existent pour
Tout / Dictées / Imports (Notes et Appels viendront avec leurs lots), le
transcript est cliquable, les locuteurs se renomment et se fusionnent,
« Proposer les prénoms » et les exports sont branchés. Le fichier importé n'est
pas copié : la bibliothèque stocke l'index et le transcript JSON. Mémorisation
des prénoms par contact : pas encore.

**Import assisté (02/10/2026)** : l'import a son propre onglet dans les
réglages — fichiers par glisser-déposer, modèle, diarisation, nombre de
personnes attendues, nettoyage LLM, estimation durée + coût, progression en
arrière-plan et annulation. La bibliothèque reste l'endroit où l'on relit, et
le bouton « Importer » y reste disponible.

**Nettoyage (01/10/2026)** : après chaque import, un nettoyage éditorial
automatique corrige ponctuation, majuscules, noms propres et « euh », avec le
vocabulaire des réglages comme glossaire. Décochable dans Réglages →
Traitement. Le transcript brut d'avant nettoyage est conservé
(`<id>.brut.json`) ; l'interface affiche « nettoyé (coût) » et le brut servira
de base à un futur bouton « voir le brut / revenir au brut ».

## 7. Appels à plusieurs participants

Fait à connaître : **l'appli d'appel mixe tous les interlocuteurs distants en une
seule piste** avant la carte son. Impossible de récupérer les flux individuels.

| Scénario | Live | Après l'arrêt |
|---|---|---|
| 2 pistes, appel à N | « Moi » / « Interlocuteur » | Diarisation de la piste système → Locuteur 1…N, puis prénoms |
| Import mono à N | — | Diarisation N locuteurs |
| 1 voix | — | Pas de diarisation |

Sans rien demander : plafond de locuteurs généreux (8), puis **passe de fusion
LLM** si sur-découpage (« 5 locuteurs détectés → 4 après nettoyage »), puis
outils de correction. Réglage avancé optionnel : « cette réunion = N personnes »
pour verrouiller le compte.

Cas béni : un enregistrement **par participant** (Zoom cloud) importé comme
groupe de pistes → séparation parfaite, zéro diarisation.

## 8. Live et agent

- **Live simple (recommandé)** : OpenRouter n'a pas de streaming → découpage en
  tranches de 3-8 s avec VAD, envoi parallèle, affichage au fil de l'eau.
  Latence 1-3 s. Coût ~0,05-0,20 $ pour 1 h d'appel (voir doc modèles).
- **Vrai streaming** (partials ~300 ms) : WebSocket direct Deepgram ou xAI. Plus
  de travail + une clé en plus → plus tard, seulement si le live sert vraiment.
- **Diarisation live** : gratuite avec le split micro/système (2 flux STT →
  « Moi » / « Interlocuteur »). Au-delà de 2 interlocuteurs, étiquettes précises
  à l'arrêt ; option v4 : diarisation glissante sur les 60 dernières secondes.
- **Agent « qu'est-ce que je réponds ? »** : buffer glissant du transcript
  (5-10 min) + raccourci global → modèle rapide et pas cher (Gemini 3.5 Flash
  Lite, DeepSeek V4 Flash, GPT-5 mini). Réponse 1-3 s, ~0,002-0,01 $ par question
  avec cache de contexte. Option 100 % locale (Ollama/llama.cpp) pour la
  confidentialité. Le LLM n'a pas besoin des noms : les tours de parole suffisent.

## 9. Fidélité UI/UX

Composants réutilisés :

- **`Overlay` (la pilule)** : indicateur d'enregistrement d'appel
  (`Enregistrement — WhatsApp` + chrono + `LevelBars` + Stop/Pause) et
  progression de transcription.
- **`Chip`, `IconButton`, `Spinner`, `LevelBars`** (`ui/widgets.py`).
- Style via `theme.app_qss` / `theme.palette` — thème sombre et clair existants.
- **Popup d'appel détecté** : même langage visuel que la pilule, jamais une boîte
  de dialogue Windows.
- Règles : aucun modal bloquant, toute opération annulable, erreurs = une ligne
  discrète + « Réessayer », tout ce qui est technique rangé dans
  **Réglages → Traitement**, en `Automatique` par défaut.

L'onglet **Réglages → Traitement** existe depuis le 30/09/2026 : modèle
d'import, taille des tranches, tranches en parallèle, raccord LLM des
locuteurs.

## 10. Packaging Windows (`Vox.spec`)

- `hiddenimports` : `pyaudiowpatch`, `pycaw`, `psutil`, `comtypes` (dépendance
  pycaw).
- `binaries` : `ffmpeg.exe` fourni par le wheel `imageio-ffmpeg` (rien à
  installer côté utilisateur).
- **Ne pas exclure `PySide6.QtMultimedia`** (déjà utilisée par l'historique).
  `QtMultimediaWidgets` reste exclu tant qu'on ne fait pas de vidéo.
- Attention : Vox n'est pas signé → l'antivirus peut râler sur les nouvelles
  DLL (WASAPI loopback). Tester l'installeur après ajout.

## 11. Roadmap

| Lot | Contenu | Effort indicatif |
|---|---|---|
| **1** | Import + routeur + diarisation + Bibliothèque + transcript cliquable + renommage + export | 2-3 j |
| **2** | Capture système + 2 pistes + modes micro/système/les deux + pilule d'enregistrement + transcription auto à l'arrêt | 2-3 j |
| **3** | Détection d'appels + popup + allowlist + bouton manuel | 1-2 j |
| **4** | Live + sous-titres + agent au raccourci | 3-4 j |
| **v3+** | Diarisation locale (pyannote), empreintes vocales (prénoms auto), streaming WS, vidéo d'écran | selon envie |

Chaque lot est utilisable seul.

## 12. Décisions ouvertes

1. Popup d'appel : toujours demander (recommandé) ou mode « confiance » par app ?
2. Transcription automatique à l'arrêt : oui par défaut, ou bouton manuel ?
3. Nom auto des enregistrements : `Appel WhatsApp — 29/09 14:32` convient ?
4. « Dictée » reste strictement inchangée, avec Notes / Appels / Imports à côté ?

## 13. Risques et points de vigilance

- **Consentement** : enregistrer une conversation privée peut nécessiter l'accord
  des participants (France). Prévoir une mention/info dans l'UI.
- **Faux positifs de détection d'appel** : ne jamais démarrer d'enregistrement
  sans action de l'utilisateur (sauf mode confiance explicite).
- **Chevauchements de parole** : principale source d'erreur de diarisation, en
  particulier dans les réunions. Mitigation : outils de fusion, passe LLM.
- **Qualité du son système** : dépend du casque (Bluetooth HFP), du mode exclusif,
  des réglages Windows.

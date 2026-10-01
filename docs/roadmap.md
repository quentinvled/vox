# Feuille de route — assistant audio

Fichier de suivi : on coche ici, on ne perd rien en route. Les documents de
conception sont [`assistant-audio.md`](assistant-audio.md) (produit) et
[`modeles-diarisation.md`](modeles-diarisation.md) (modèles et coûts).

Conventions :

- `[x]` fait et vérifié par les tests ; `[~]` en cours ; `[ ]` à faire.
- « **Test** » = étape qui attend un retour utilisateur avant de continuer.

## Lot 1 — Importer et transcrire avec diarisation (en cours)

| Étape | Contenu | État |
|---|---|---|
| 1.1 | Décodage ffmpeg, sondage, détection des silences, découpage (`src/vox/audiofiles.py`) | [x] |
| 1.2 | Routeur automatique mono / canaux (`src/vox/routing.py`) | [x] |
| 1.3 | Modèle de transcript, fusion des tranches, raccord des locuteurs, exports (`src/vox/transcript.py`) | [x] |
| 1.4 | Options de diarisation par fournisseur + `verbose_json` (`src/vox/api.py`) | [x] |
| 1.5 | Orchestration complète et ligne de commande (`src/vox/imports.py`, `vox --import`) | [x] |
| 1.6 | Tests (42) : découpage, routeur, fusion, exports, import de bout en bout, raccord locuteurs | [x] |
| 1.7 | Passe LLM de fusion des locuteurs (sur-découpage entre tranches) | [x] |
| 1.8 | Stockage : transcript dans la bibliothèque (index + fichiers) | [x] |
| 1.9 | UI : bouton Importer, progression, transcript cliquable, renommage, export | [x] |
| 1.10 | Nettoyage éditorial d'un transcript (`vox --clean-transcript`, contexte + glossaire) | [x] |
| 1.11 | Prénoms des locuteurs : déduction LLM (`--name-speakers`) et renommage explicite (`--rename-speakers`) | [x] |

**Test n°1 (en cours, VPS)** — premier essai réel le 30/09 sur un appel de
33 min à 3 personnes (`Call KH Route.m4a`, 97 min au total dont seules les
33 premières minutes utiles) :

- `mai-transcribe-2` : 33 min transcrites et diarisées en **18 s**, coût
  **0,055 $**, 300 segments, aucune erreur ;
- le raccord par recouvrement seul produisait 8 étiquettes ; la passe LLM
  (avec `--import-speakers 3`) retrouve les **3 personnes**, labels cohérents
  de bout en bout ;
- limite découverte : `mai-transcribe-2` refuse les entrées > ~7-8 Mo
  (400 « does not support large audio inputs ») → encodage mp3 96 kb/s et
  tranches de 12 min (le flac plafonnait à 7 min) ;
- les deux canaux de ce fichier portent le même son (corrélation 0,85,
  co-activité 0,95) → diarisation du mix, pas de séparation par canal.

À vérifier par Quentin : orthographe des noms propres (« Calis »), pertinence
du 3ᵉ locuteur (il apparaît à 11:49), et s'il faut un autre modèle pour les
noms (Gemini Transcribe, ~0,22 $ pour 33 min).

**Test n°1 bis (prêt, à faire sur Windows)** — la bibliothèque est désormais
utilisable dans l'app : bouton **Importer**, progression dans la fenêtre,
transcript **cliquable** (clic = écouter à partir de la réplique), pastilles de
locuteur, renommage et fusion des locuteurs, « Proposer les prénoms », exports
md/txt/srt/vtt/json, filtres Tout/Dictées/Imports. À vérifier : importer un
vocal WhatsApp (1 voix, pas de diarisation attendue) et un appel à 2-3, puis
renommer les locuteurs et exporter.

Mesures du 30/09 en comparant les moteurs (voir
[`modeles-diarisation.md`](modeles-diarisation.md) §10) : Deepgram trouve les
mêmes 2 voix que MAI sur les 10 premières minutes ; **Grok et Gemini ne
diarisent pas du tout via OpenRouter** (1 segment global, aucun locuteur) — ils
restent utilisables en texte seul, avec un avertissement dans le transcript.

## Lot 2 — Enregistrer (Windows)

| Étape | Contenu | État |
|---|---|---|
| 2.1 | Capture du son système (WASAPI loopback) | [ ] |
| 2.2 | Double piste micro + système, fichiers séparés, mixage à l'export | [ ] |
| 2.3 | Modes micro / système / les deux dans le menu et les réglages | [ ] |
| 2.4 | Pilule d'enregistrement (chrono, niveaux, pause, arrêt) | [ ] |
| 2.5 | Transcription automatique à l'arrêt (réglage, coût affiché) | [ ] |
| 2.6 | Pièges : Bluetooth HFP, mode exclusif WASAPI, périphérique débranché | [ ] |

**Test n°2** : enregistrer un appel réel, vérifier les deux pistes et la
diarisation « gratuite » (Moi / Interlocuteur).

## Lot 3 — Détecter les appels

| Étape | Contenu | État |
|---|---|---|
| 3.1 | Détection Windows : sessions audio (`pycaw`) + process + fenêtres | [ ] |
| 3.2 | Popup non bloquant (Enregistrer / Plus tard / Ne plus demander) | [ ] |
| 3.3 | Allowlist d'apps réglable | [ ] |
| 3.4 | Entrée « Enregistrer un appel » dans le menu | [ ] |

**Test n°3** : lancer un appel WhatsApp, vérifier le popup et le démarrage en un
clic, puis le non-déclenchement sur un simple enregistreur vocal.

## Lot 4 — Live et agent

| Étape | Contenu | État |
|---|---|---|
| 4.1 | Transcription live par tranches de 3-8 s (VAD) | [ ] |
| 4.2 | Sous-titres flottants pendant l'appel | [ ] |
| 4.3 | Agent au raccourci (« qu'est-ce que je réponds ? ») sur buffer glissant | [ ] |
| 4.4 | Option agent local (Ollama / llama.cpp) | [ ] |

**Test n°4** : pendant un appel, vérifier la latence du live et la pertinence de
l'agent.

## Plus tard (non planifié)

- Diarisation locale (pyannote) en option, empreintes vocales → prénoms
  automatiques.
- Streaming WebSocket direct (Deepgram / xAI) si le live par tranches ne suffit
  pas.
- Enregistrement vidéo d'écran.
- Recherche plein texte dans la bibliothèque (SQLite FTS).

## Journal

- **01/10/2026 (imports)** — le **nettoyage éditorial devient automatique après
  chaque import** (ton « Nettoyer » via le modèle de chat, glossaire repris du
  vocabulaire des réglages). Décochable dans Réglages → Traitement
  (`clean_imports`), désactivable ponctuellement avec `--import-no-clean`. Le
  coût du nettoyage s'ajoute à celui de l'import (mesuré : 0,0013 $ pour 1 min,
  soit ~0,08 $/h) et le **transcript brut est conservé** à côté du nettoyé
  (`<id>.brut.json`), pour comparaison. Interruptible en cours d'import : on
  garde alors le texte brut. Vérifié en vrai : 10 « euh » → 0 sur un extrait.
- **01/10/2026** — la **reformulation devient active par défaut** (ton
  « Nettoyer ») : hésitations, répétitions, faux départs et ponctuation sont
  nettoyés avant l'insertion. Les installations existantes basculent une seule
  fois (`extras.reword_default_on`) ; si l'utilisateur la coupe ensuite, son
  choix est respecté. En cas d'échec du modèle de chat, le texte brut est inséré
  et une ligne discrète le signale. Coût marginal (~0,001-0,003 $/dictée) et
  latence ajoutée de 0,5 à 2 s selon le modèle de chat.
- **30/09/2026 (nuit)** — étapes 1.8 et 1.9 terminées : **bibliothèque** des
  imports (index JSONL + transcript JSON, le fichier source n'est jamais copié
  ni supprimé), écran Bibliothèque unifié (dictées + imports), bouton
  **Importer** avec progression et annulation, transcript **cliquable**
  (clic → lecture à partir de la réplique), pastilles de locuteur, renommage et
  fusion, « Proposer les prénoms », exports md/txt/srt/vtt/json, filtres
  Tout/Dictées/Imports, onglet **Réglages → Traitement** (modèle d'import,
  taille des tranches, parallélisme, raccord LLM). CLI : `vox --import` range
  désormais dans la bibliothèque (`--import-no-save` pour l'éviter) et
  `vox --library` liste les imports. 73 tests, dont des tests Qt hors écran.
- **30/09/2026 (soir)** — corrections issues du test réel : le **mp3 devient le
  conteneur par défaut** (l'API plafonne le poids vers 7-8 Mo, pas la durée) et
  un flac refusé pour cause de poids repart automatiquement en mp3, jamais en
  wav ; Grok et Gemini mesurés **non diarisants** via OpenRouter (retirés de la
  table de diarisation, avertissement dans le transcript) ; `deepgram/nova-3`
  documenté (10 min de flac refusées, 10 min de mp3 acceptées).
- **30/09/2026 (prénoms)** — étape 1.11 : détection des prénoms dans l'appel KH.
  « Jonas » est cité 5 fois, « Quentin » 1 fois (24:51), « Mikael » jamais dans
  les 33 premières minutes (déduit par élimination). L'inférence LLM avait
  inversé Jonas et Quentin (elle nommait « Jonas » celui qui demande « t'es là,
  Jonas ? ») : le renommage explicite `--rename-speakers` a été ajouté pour
  appliquer la déduction vérifiée à la main. Mapping retenu : Jonas =
  locuteur 1 (transporteur), Quentin = locuteur 2 (éditeur), Mikael =
  locuteur 3 (éditeur).
- **30/09/2026 (suite)** — nettoyage éditorial (étape 1.10) : les 33 min
  corrigées en 56 s pour **0,10 $** (Claude Haiku 4.5, 5 blocs parallèles).
  « Calis » → « Khalis » (16 occurrences), répétitions et « euh » supprimés
  (−9 % de mots), ponctuation et majuscules rétablies, horodatages et
  locuteurs intacts. Le premier essai (Gemini 3.5 Flash) avait coûté 0,33 $
  et perdu un bloc sur quatre : d'où le recoupage automatique des blocs.
- **30/09/2026** — premier import réel (appel KH Route, 33 min, 3 personnes) :
  18 s, 0,055 $, 3 locuteurs après la passe LLM de raccord (8 sans elle).
  Corrections issues du terrain : limite de taille de `mai-transcribe-2`
  (mp3 12 min), profil des canaux (co-activité), options `--import-limit`,
  `--import-codec`, `--import-speakers`. 42 tests.
- **29/09/2026** — Lot 1 (étapes 1.1 à 1.6) implémenté : routeur, découpage aux
  silences, diarisation via OpenRouter, fusion des tranches, exports, CLI
  `vox --import`, 33 tests. Décision par défaut : `microsoft/mai-transcribe-2`
  (0,10 $/h) ; alternatives Grok et Gemini Transcribe testables via
  `--import-model`. `imageio-ffmpeg` ajouté aux dépendances (ffmpeg embarqué,
  rien à installer).

## Points de vigilance

- Les modèles plafonnent : Gemini 30 min avec diarisation, MAI-Transcribe 2
  ~32 min (bug connu). Le découpage à 10-12 min les contourne : ne pas le monter
  sans test. Limite de poids mesurée : ~7-8 Mo par requête, d'où le mp3 par
  défaut.
- Grok STT et Gemini Transcribe ne rendent **aucun locuteur** via OpenRouter
  (mesuré le 30/09) : les choisir pour la diarisation ne produit qu'un
  « Locuteur » unique, Vox l'affiche désormais en avertissement.
- Une phrase à cheval sur une coupe est dédupliquée ; les locuteurs ne sont
  raccordés que si quelqu'un parle dans la zone de recouvrement (2,5 s). Sinon
  on sur-découpe volontairement : fusion en un clic (étape 1.7/1.9).
- La diarisation ne fonctionne que via OpenRouter (clé OpenRouter requise, même
  si la dictée utilise Groq ou OpenAI).
- Consentement : enregistrer une conversation privée peut nécessiter l'accord
  des participants.

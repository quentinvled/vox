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
| 1.6 | Tests (33) : découpage, routeur, fusion, exports, import de bout en bout | [x] |
| 1.7 | Passe LLM de fusion des locuteurs (sur-découpage entre tranches) | [ ] |
| 1.8 | Stockage : transcript dans la bibliothèque (index + fichiers) | [ ] |
| 1.9 | UI : bouton Importer, progression, transcript cliquable, renommage, export | [ ] |

**Test n°1 (à faire par Quentin, Windows)** — vérifier la qualité avant de
construire l'UI dessus :

```powershell
uv sync
# 1) A/B des modèles sur un vrai fichier, en regardant le coût affiché :
uv run vox --import "C:\chemin\appel.m4a"                              # MAI-Transcribe 2 (défaut)
uv run vox --import "C:\chemin\appel.m4a" --import-model x-ai/grok-stt-1.0
uv run vox --import "C:\chemin\appel.m4a" --import-model google/gemini-3.5-transcribe
# 2) Un vocal WhatsApp mono (doit donner un seul locuteur, sans sur-découpage)
# 3) Un appel à 4-5 personnes (vérifier sur-découpage puis fusion)
```

À regarder : fidélité du français, justesse des attributions, nombre de
locuteurs détectés, coût réel, durée. Les résultats sont écrits en `.md` et
`.json` à côté du fichier source (ou dans `--import-out`).

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

- **29/09/2026** — Lot 1 (étapes 1.1 à 1.6) implémenté : routeur, découpage aux
  silences, diarisation via OpenRouter, fusion des tranches, exports, CLI
  `vox --import`, 33 tests. Décision par défaut : `microsoft/mai-transcribe-2`
  (0,10 $/h) ; alternatives Grok et Gemini Transcribe testables via
  `--import-model`. `imageio-ffmpeg` ajouté aux dépendances (ffmpeg embarqué,
  rien à installer). Prochaine étape après le test n°1 : 1.7 puis 1.8-1.9.

## Points de vigilance

- Les modèles plafonnent : Gemini 30 min avec diarisation, MAI-Transcribe 2
  ~32 min (bug connu). Le découpage à 10 min les contourne : ne pas le monter
  sans test.
- Une phrase à cheval sur une coupe est dédupliquée ; les locuteurs ne sont
  raccordés que si quelqu'un parle dans la zone de recouvrement (2,5 s). Sinon
  on sur-découpe volontairement : fusion en un clic (étape 1.7/1.9).
- La diarisation ne fonctionne que via OpenRouter (clé OpenRouter requise, même
  si la dictée utilise Groq ou OpenAI).
- Consentement : enregistrer une conversation privée peut nécessiter l'accord
  des participants.

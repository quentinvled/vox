# Diarisation et modèles STT (référence, non implémenté)

Relevé le **29/09/2026** — les prix et modèles bougent, revérifier avant de
décider. Voir aussi [`assistant-audio.md`](assistant-audio.md) pour la partie
produit.

## 1. Ce que sait faire l'API OpenRouter

Endpoint : `POST https://openrouter.ai/api/v1/audio/transcriptions` (base
`https://openrouter.ai/api/v1`).

- **JSON base64** (`input_audio: {data, format}`) : pas de limite de 25 Mo,
  offload en streaming. Formats : `wav`, `mp3`, `flac`, `m4a`, `ogg`, `webm`,
  `aac`.
- **Multipart** (`-F file=@...`) : limite 25 Mo (≈ 52 min de MP3 64 kbps, ≈ 2 h
  d'Opus 24 kbps). Le `prompt` y est accepté mais ignoré.
- **Timeout amont ~60 s de traitement par requête** → un appel d'1 h doit être
  découpé de toute façon. Le découpage n'augmente pas le coût (facturation à la
  durée d'audio).
- `response_format: "verbose_json"` + `timestamp_granularities: ["segment","word"]`
  → `segments` et `words` avec `start`, `end`, et `speaker` quand le fournisseur
  diarise. Certains fournisseurs refusent `verbose_json` (ex.
  `openai/gpt-4o-transcribe`, `microsoft/mai-transcribe-1.5`).
- **Diarisation = option spécifique au fournisseur**, à passer sous
  `provider.options.<tag>` (le `tag` vient de
  `/api/v1/models/<id>/endpoints`). Les paramètres normalisés (`language`,
  `temperature`, `response_format`, `timestamp_granularities`) restent à la
  racine. Certains fournisseurs filtrent les options inconnues en silence.
- `usage.cost` est renvoyé : **c'est la source de vérité pour le coût réel**.
- Découverte des modèles : `GET /api/v1/models?output_modalities=transcription`.
- Pas de streaming : l'endpoint est requête/réponse. Pour du live, découper.
- BYOK supporté (clé fournisseur en direct → frais de plateforme OpenRouter
  uniquement).

Exemple diarisation Azure (MAI-Transcribe 2) :

```json
{
  "model": "microsoft/mai-transcribe-2",
  "input_audio": { "data": "<base64>", "format": "mp3" },
  "response_format": "verbose_json",
  "timestamp_granularities": ["segment", "word"],
  "provider": { "options": { "azure": { "diarization": { "enabled": true } } } }
}
```

## 2. Ce qui fait une bonne diarisation (par ordre d'impact)

1. **Stéréo / multicanal** : si chaque voix est sur un canal, la séparation est
   parfaite (Grok STT et Deepgram gèrent le multicanal). À détecter et exploiter
   automatiquement.
2. **Annoncer le nombre de locuteurs** (ou un plafond) : levier n°2 après le
   canal. La diarisation segmente puis **regroupe** ; sans contrainte, le modèle
   sur-découpe (une personne devient 2-3 locuteurs) ou fusionne. Donner N fixe le
   regroupement, surtout sur les fichiers courts ou en mono dégradé.
   - Préférer un **plafond** à un compte exact : un plafond se trompe « vers le
     haut » sans casser ; un compte exact faux fait pire que l'auto (dire 2 pour
     3 personnes fusionne deux voix).
   - Support : Azure `maxSpeakers` 2-35 (Fast API, à tester via OpenRouter) ;
     Gemini Transcribe : 8 max ; MAI-Transcribe 2 : `diarization.enabled` seul
     documenté ; Grok, Deepgram, Fish Audio : comptage automatique, pas de
     paramètre de nombre documenté.
   - **Collecte implicite côté Vox** (principe « zéro contrainte ») : appel 1:1
     détecté → 2 ; appel de groupe → nombre mémorisé par contact/groupe ;
     réglage avancé « cette réunion = N ». Un bouton « Corriger le nombre »
     dans le transcript permet de le donner à la main sans formulaire.
3. **Découper aux silences** (VAD), jamais au milieu d'une phrase.
4. **Audio propre** : 16 kHz+, peu de ré-encodage. Un vocal WhatsApp (Opus) est
   très bien ; la bande téléphonique 8 kHz est plus dure.
5. **Chevauchements** : principale source d'erreur restante (avec les tours très
   courts type « ouais », « ok »).
6. **Passe LLM de post-traitement** : unifier les étiquettes, fusionner les
   sur-découpages, corriger les noms propres, proposer les prénoms.
7. **Keyterms / vocabulaire** : prénoms, jargon, noms d'entreprise (Grok :
   `keyterm`, Azure : keyword biasing, Whisper : `prompt` via options
   fournisseur).

## 3. Modèles avec diarisation

| Modèle | Option fournisseur | Limites connues | $/h |
|---|---|---|---|
| `x-ai/grok-stt-1.0` (tag `xai`) | `diarize`, multicanal, `keyterm` | long fichier OK (à tester) | **0,10** |
| `microsoft/mai-transcribe-2` (tag `azure`) | `diarization.enabled` | **bug : 503 au-delà de ~32 min avec diarisation** ; 60 langues, #1 FLEURS | **0,10** |
| `meta/muse-voice-transcribe-1.0` (tag `meta`) | speaker-aware | — | 0,18 |
| `assemblyai/universal-3-5-pro` (tag `assemblyai`) | Sync API | **120 s max par clip** ; promo 50 % signalée | 0,23 |
| `deepgram/nova-3` (tag `deepgram`) | `diarize` (+ `punctuate`, `smart_format`) | diarisation incluse en batch | 0,26 |
| `fish-audio/transcribe-1-pro` (tag `fish-audio`) | locuteurs inline dans le texte | — | 0,36 |
| `google/gemini-3.5-transcribe` (tag `google-ai-studio`) | diarisation native | **30 min max avec diarisation** (1 h sans) ; 8 locuteurs | ~0,40 |
| `google/chirp-3` (tag `google-vertex`) | diarisation | — | 0,96 |

Détails utiles :

- **Gemini Transcribe** est facturé aux tokens (2 $/M entrée, 12 $/M sortie).
  L'audio Gemini ≈ 32 tokens/s → ~0,23 $/h d'entrée + ~0,15 $/h de sortie.
  C'est le plus « qualitatif » de la liste pour le français, mais le plus cher.
- **MAI-Transcribe 2** : le catalogue OpenRouter l'expose à 0,10/h (unité
  horaire, comme `microsoft/mai-transcribe-1.5` à 0,36). Attention à
  l'interprétation des unités : `api.per_hour_from_catalogue()` dans Vox gère ce
  cas (valeurs aberrantes = déjà horaires).
- **Grok STT** : le meilleur rapport simplicité/prix (diarisation + multicanal +
  long fichier). Le champ fournisseur s'appelle `diarize`.
- **Deepgram** : seul fournisseur documenté comme filtrant les options (`diarize`,
  `punctuate`, `smart_format`, `detect_language`). Diarisation réputée fiable ;
  en direct, Deepgram facture la diarisation en supplément (+0,12 $/h) et le
  streaming est plus cher (~0,29-0,46 $/h).
- La plupart de ces modèles sont **plus récents que Whisper** et meilleurs sur
  les appels ; Whisper reste utile pour le prix.

## 4. Modèles sans diarisation (utiles pour le prix)

| Modèle | $/h | Remarque |
|---|---|---|
| `openai/whisper-large-v3-turbo` | 0,012 | le moins cher, correct |
| `qwen/qwen3-asr-0.6b`, `nvidia/nemotron-3.5-asr-*` | 0,012 | ASR léger |
| `openai/whisper-large-v3` | 0,027 | valeur sûre, 99 langues |
| `nvidia/parakeet-tdt-0.6b-v3` | 0,09 | rapide, multilingue |
| `openai/gpt-4o-mini-transcribe` | ~0,18 | tokens |
| `openai/gpt-transcribe` | 0,27 | — |
| `openai/whisper-1` / `gpt-4o-transcribe` | ~0,36 | tokens pour le second |

Idée à garder sous le coude : ASR pas cher (Whisper/Parakeet) + **diarisation
locale** (pyannote community-1) → ~0,03 $/h, mais ~2-3 Go de dépendances
(torch) et un DER moins bon sur les appels (CALLHOME ~27 % en open source ;
les API commerciales font mieux). À réserver à une option « local ».

## 5. Coût d'un appel d'1 h

| Configuration | Coût |
|---|---|
| Grok / MAI-Transcribe 2 | **0,10 $** |
| Deepgram Nova-3 | 0,26 $ |
| Gemini 3.5 Transcribe | ~0,40 $ |
| + nettoyage LLM du transcript (1 h ≈ 12-15k tokens) | 0,01-0,05 $ |
| Live en tranches de 3-8 s (VAD, ~50 % de parole) | 0,05-0,20 $ |

À titre de repère, la dictée actuelle de Vox coûte déjà ~0,10 $/h. Le nombre de
locuteurs ne change pas le prix (facturation à la durée).

## 6. Prénoms des locuteurs

La diarisation détecte *combien* de voix et les regroupe, mais **ne connaît
personne** (aucune base de voix).

1. **Renommage manuel** dans l'UI, mémorisé par fichier puis par contact.
2. **Suggestion LLM** : déduire les prénoms des échanges (« merci Marie »,
   « Paul, tu peux… »), avec bouton « Proposer les prénoms ».
3. **Empreintes vocales (v3, local)** : 30-60 s de référence par contact
   (pyannote community-1 / SpeechBrain ECAPA / 3D-Speaker), matching par
   similarité cosinus → attribution automatique, y compris d'un appel à l'autre.

## 7. Appels à plusieurs

- L'appli d'appel mixe les interlocuteurs distants en une piste → seule la
  diarisation peut les séparer.
- Diariser **la piste système uniquement** (quand on a deux pistes) : ta voix ne
  pollue pas le clustering, moins de chevauchements → meilleur résultat.
- Sur-découpage fréquent avec les tours courts → passe de fusion LLM + outils
  manuels (fusionner, réassigner).
- Live : « Moi » / « Interlocuteur » seulement ; étiquettes précises à l'arrêt.

## 8. Protocole de test avant de choisir

Tester 2-3 modèles (Grok + MAI-Transcribe 2, + Gemini Transcribe) sur **trois
vrais fichiers** :

1. un vocal WhatsApp mono de 1-2 min (cas 1 voix) ;
2. un appel à 2 d'environ 10 min, en mono ;
3. une réunion à 4-5 de 20-30 min, en 2 pistes.

Mesurer pour chacun : `usage.cost` réel, temps de traitement, qualité
d'attribution des locuteurs (à l'œil), et tenue du français. C'est ce test qui
tranche, pas les benchmarks.

## 9. Notes d'intégration dans Vox

- `src/vox/api.py` : le client STT existe déjà (OpenRouter JSON base64 +
  fournisseurs OpenAI-compatibles). À ajouter : `response_format`,
  `timestamp_granularities`, `provider.options` (diarisation), et un mode
  « tranches ».
- `PROMPT_AWARE_PROVIDERS` : liste des fournisseurs qui acceptent `prompt` —
  étendre la même logique pour les options de diarisation par `tag`.
- `per_hour_from_catalogue()` : gère déjà l'ambiguïté des unités du catalogue.
- Le modèle de diarisation doit être distinct du modèle de dictée (réglage
  séparé, défaut automatique).

# Vox

Dictée vocale globale pour Windows : maintiens **Ctrl + Maj**, parle, relâche —
le texte s'écrit tout seul dans la fenêtre active.

Transcription via OpenRouter (Whisper, Qwen-ASR, Gemini…), reformulation LLM
optionnelle, statistiques d'usage et historique audio local.

## Télécharger

1. Ouvre la page des versions : **[github.com/quentinvled/vox/releases/latest](https://github.com/quentinvled/vox/releases/latest)**
2. Télécharge le fichier **`Vox-Setup-<version>.exe`**.
3. Double-clique dessus. Windows affichera peut-être « Windows a protégé votre
   PC » (l'installateur n'est pas signé numériquement) : clique sur
   **Informations complémentaires**, puis **Exécuter quand même**.
4. L'installation se fait dans ton dossier personnel, **sans droits
   administrateur**. Des raccourcis sont créés sur le Bureau et dans le Menu
   Démarrer.

Un fichier `.sha256` est publié à côté de l'installateur si tu veux vérifier
l'intégrité du téléchargement.

## Premier lancement

1. Une petite icône apparaît près de l'horloge (derrière le chevron `^`).
2. Ouvre les réglages (clic droit sur l'icône → **Réglages**).
3. Colle une **clé OpenRouter** (à créer sur
   [openrouter.ai/settings/keys](https://openrouter.ai/settings/keys), après
   avoir ajouté quelques dollars de crédit — la dictée coûte ~0,10 $/heure).
4. Clique sur **Tester**, puis **Enregistrer**.
5. Maintiens **Ctrl + Maj**, parle, relâche : le texte s'insère.

## Mises à jour

Vox vérifie automatiquement les nouvelles versions au démarrage et deux fois
par jour. Quand une version plus récente est publiée, une notification et une
entrée « Mise à jour disponible » apparaissent dans le menu : un clic ouvre la
page de téléchargement. Rien n'est installé automatiquement.

## Désinstaller

**Paramètres → Applications → Vox → Désinstaller**. Il te sera demandé si tu
veux aussi supprimer ton historique et ta clé API.

## Confidentialité

Tout est local : réglages et historique vivent dans `%LOCALAPPDATA%\Vox`.
Seul l'audio de chaque dictée est envoyé au service de transcription choisi,
via OpenRouter. Voir `tools/installer/LISEZ-MOI.txt` pour le détail des
données.

## Développement

```bash
uv sync
uv run vox                 # lance l'application
uv run vox --list-devices  # liste les micros
uv run vox --stats         # statistiques en ligne de commande
```

Construire l'installateur Windows :

```bash
uv run python tools/build_installer.py --rebuild
```

Publier une version (voir [`RELEASING.md`](RELEASING.md)) :

```bash
uv run python tools/release.py 0.2.0
```

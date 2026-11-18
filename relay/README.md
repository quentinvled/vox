# Relais Vox (vox.vled.fr)

Dicter depuis le téléphone : on parle sur le téléphone, le texte s'écrit sur
l'ordinateur, dans la fenêtre active, avec le même traitement qu'une dictée au
clavier (transcription, nettoyage, mode consigne).

```
téléphone (page web)  ⇄  relais (ce dossier)  ⇄  Vox sur l'ordinateur
```

Le relais ne fait que transmettre. Il ne garde rien sur disque, ne transcrit
rien et ne voit aucune clé d'API : la transcription reste faite par Vox, sur
l'ordinateur, avec ses propres réglages.

## Contenu

| Chemin | Rôle |
|---|---|
| `vox_relay/server.py` | Serveur aiohttp : page du téléphone et canal WebSocket `/ws` |
| `vox_relay/rooms.py` | Salons (un ordinateur, ses téléphones), autorisations, limites |
| `web/` | Page du téléphone, sans dépendance ni étape de construction |
| `web/js/main.js` | Écran, gestes du bouton, états renvoyés par l'ordinateur |
| `web/js/link.js` | Connexion au relais, reconnexion automatique, file d'attente |
| `web/js/mic.js`, `capture-worklet.js` | Micro : 16 kHz mono, paquets de 80 ms |
| `web/sw.js` | Ouverture instantanée depuis le cache, mise à jour en arrière-plan |
| `tests/` | Tests du relais (`uv run pytest`) |
| `tools/make_icons.py` | Régénère les PNG à partir de `web/icons/icon.svg` |

Côté ordinateur : `src/vox/phone.py` (liaison) et `src/vox/ui/phone_window.py`
(appairage), dans le dépôt Vox.

## Appairage et sécurité

1. Vox crée un **secret d'ordinateur** (`phone_secret` dans `settings.json`).
   L'identifiant public du salon en est dérivé :
   `base64url(sha256("vox-room:" + secret))[:22]`. Seul le détenteur du secret
   peut donc occuper la place de l'ordinateur.
2. « Relier un téléphone » crée un **jeton de téléphone** aléatoire et affiche
   `https://vox.vled.fr/#<salon>.<jeton>` en code QR. Le fragment `#…` n'est
   jamais envoyé au serveur par le navigateur.
3. Vox ne garde que l'**empreinte** du jeton
   (`sha256("vox-phone:" + jeton)[:32]`) et l'annonce au relais. Un téléphone
   n'est relayé que si l'empreinte de son jeton est annoncée.
4. Retirer un téléphone dans Vox (ou « Délier » sur le téléphone) retire
   l'empreinte : le relais ferme aussitôt la connexion (code 4003) et le
   téléphone revient à l'écran d'accueil.

Le chiffrement est celui de HTTPS/WSS jusqu'au relais. L'audio transite par le
relais sans y être conservé.

## Protocole (`/ws`)

Messages texte : JSON avec un champ `t`. Audio : messages binaires, PCM 16 bits
little-endian, 16 kHz, mono, du téléphone qui parle vers l'ordinateur.

Premier message de chaque connexion (10 s au plus) :

```json
{"t": "auth", "role": "computer", "secret": "…", "allowed": ["<empreinte>", …], "name": "PC de Quentin"}
{"t": "auth", "role": "phone", "room": "<salon>", "token": "…", "name": "iPhone"}
```

| Sens | Message | Effet |
|---|---|---|
| relais → ordinateur | `ready` | Authentifié |
| relais → ordinateur | `phone` `{id, name, online}` | Téléphone en ligne / parti |
| ordinateur → relais | `allow` `{allowed}` | Nouvelle liste autorisée |
| relais → téléphone | `ready` `{id, computer: {online, name}}` | Authentifié |
| relais → téléphone | `computer` `{online, name}` | Ordinateur arrivé / parti |
| téléphone → ordinateur | `start` `{mode}` | Début de prise (`dictation` ou `instruction`) |
| téléphone → ordinateur | binaire | Audio de la prise |
| téléphone → ordinateur | `mode` `{mode}` | Bascule pendant la prise |
| téléphone → ordinateur | `stop` / `cancel` | Fin de prise / abandon |
| téléphone → ordinateur | `action` `{name}` | `enter`, `undo` ou `reinsert` |
| téléphone → ordinateur | `unpair` | Le téléphone se délie |
| ordinateur → téléphones | `state` `{state, text?, message?}` | `listening`, `transcribing`, `rewording`, `instructing`, `done`, `review`, `idle`, `busy`, `error` |
| ordinateur → téléphones | `notice` `{message}` | Information courte (« Rien capté. ») |

Le relais ajoute `from` (empreinte de l'expéditeur) à ce qu'il transmet à
l'ordinateur. Codes de fermeture : 4001 authentification refusée, 4003
téléphone non autorisé, 4008 salon plein, 4009 un autre ordinateur a pris la
place, 4013 relais plein.

## Développement

```bash
cd relay
uv sync
uv run pytest
PORT=8080 uv run python -m vox_relay   # http://localhost:8080
```

Vox se branche sur un relais local en mettant
`"phone_relay_url": "ws://localhost:8080/ws"` dans `settings.json`.

## Déploiement

Application Coolify « Vox relais » (projet **Vox**, serveur VLED-Hebergement),
construite depuis ce dépôt public : branche `main`, dossier de base `/relay`,
`Dockerfile`, port 8080, contrôle de santé `/healthz`, domaine
`https://vox.vled.fr` (le robot DNS crée l'entrée Cloudflare tout seul).

Un `git push` sur `main` qui touche `relay/` redéploie le relais. Après une
modification de la page, augmenter `CACHE` dans `web/sw.js` et `VERSION` dans
`web/js/main.js` : les téléphones prennent la nouvelle version à l'ouverture
suivante.

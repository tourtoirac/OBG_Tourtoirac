# Tourtoirac

Serveur WebSocket **Twisted / autobahn** : logique de jeu et état des
composants. Python >= 3.14, Poetry.

Projet frère de `OBG/Chabanas` (API HTTP) et `OBG/Raffaillac` (frontend).
Le format de `game_json` est possédé par le client : toute modification du
protocole doit être répercutée dans les deux autres dépôts.

## Commandes

```bash
poetry install
poetry run python main.py        # démarre le WebSocket sur conf.py:ws_port (9000)
poetry run pytest                # suite complète
poetry run pytest tests/test_setup.py -k rotation
poetry run coverage run -m pytest && poetry run coverage html
```

Dans cet environnement WSL, **`poetry` n'est pas installé** : utiliser
`python3 -m pytest`. `autobahn` y manque aussi, ce qui fait ignorer
automatiquement `tests/test_websocket.py` (5 tests ignorés, c'est normal).

Sur un lecteur Windows monté depuis WSL, pytest peut échouer en écrivant son
cache : `python3 -m pytest -p no:cacheprovider`.

Aucune CI : `pytest` est le seul garde-fou.

## État de la suite (vérifié le 05/10/2026)

```
288 passed, 12 failed, 5 skipped
```

Les 12 échecs sont **préexistants et hors sujet** : `tests/test_chabanas.py` (2)
et `tests/test_deck_and_card.py` (10). Ne pas les « corriger » au passage dans
une tâche sans rapport — ils fausseraient le périmètre. Si le compte bouge,
rebaser d'abord (`git stash -u`, run, `git stash pop`) avant de conclure.

## Le réacteur Twisted est global

`tests/conftest.py` démarre **un seul réacteur réel**, une fois par session de
pytest, dans un thread de fond. Le réacteur ne peut pas être redémarré : un
nouveau test qui en a besoin doit rejoindre l'instance existante au lieu d'en
créer une. C'est la contrainte d'architecture la plus facile à violer du dépôt.

`tests/test_chabanas.py` lance un faux back-end sur un port localhost aléatoire :
ni réseau ni service externe ne sont requis.

## Architecture

| Fichier                | Contenu |
| ---------------------- | ------- |
| `main.py`              | `GameWebSocketProtocol`, import du Dispatch et serveur |
| `handle_message.py`    | Dispatch : un handler par action |
| `session.py`           | **Cœur** : état de la partie, composants, diffusion |
| `Components/`          | `board`, `bag`, `card`, `counter`, `deck`, `dice`, `token`, `component` |
| `chabanas.py`          | Client HTTP vers le back-end, via `IBodyProducer` |
| `lobby.py` `user.py`   | Lobby et utilisateur connecté |
| `player_sim.py`        | Simulateur de joueur, pour essai manuel |
| `conf.py`              | Configuration, en dur (voir plus bas) |

### Ajouter une action

1. Écrire le handler dans `handle_message.py`.
2. L'importer dans la liste d'imports de `main.py` — le routage s'appuie
   dessus.

Actions actuelles : `list_sessions`, `create_session`, `join_session`,
`resume_session`, `close_session`, `list_game`, `acquire`, `release`, `move`,
`roll`, `rotate`, `flip`, `fix_positions`, `apply_setup`, `increment`,
`decrement`.

## Configuration

`conf.py` est un dictionnaire Python **en dur**, pas un `.env` : adresse et port
WS, `back_api.host`, `access_key`, et `GAMES_LIST` (`Waterloo`, `Diplomacy`,
`Vietnam`). Modifier le fichier pour changer la config ; chercher des variables
d'environnement ici est inutile.

## Composants

`Component.set_side()` renvoie `False` par défaut. `Token` le redéfinit et
exige qu'un pion soit *flippable* — c'est-à-dire qu'il possède une image de
revers (`back_src`). `Token.flip()` délègue désormais à `set_side()` : passer
par là plutôt que par une logique de face séparée.

## `game_json` et `setup`

- `setup` est une liste de `{component_id, x?, y?, side?}` décrivant
  l'installation initiale du plateau.
- Les coordonnées sont **absolues**, pas des deltas, et l'application est
  **idempotente** : réappliquer le même setup ne déplace rien deux fois.
- Les `component_id` inconnus sont ignorés sans erreur.
- `side` n'est appliqué qu'à un composant flippable.
- `game_json_state()` **retire** `setup` ; le champ n'est exposé qu'en attente,
  via `pending_setup`, puis consommé logiquement (la session ne le rejoue pas).
- Un pion déplacé par le setup adopte cette case comme **case d'origine**
  (`fix_position`).
- Rappel : `Variant.game_json` remplace le JSON de base côté Chabanas, donc un
  `setup` défini au seul niveau du jeu n'est pas hérité par les variantes.

## Conventions

- Commentaires et docstrings **en français**, contrairement à `Chabanas`.
- `session.py` est commité en **CRLF** : préserver les fins de ligne, sinon le
  diff porte sur tout le fichier.
- Les états sont sérialisés par des méthodes `return_*_json` sur les classes.

## Gotchas

- Le dépôt contient de **nombreuses modifications non commitées** issues de
  travaux antérieurs. Ne pas faire de `git reset`/`git checkout` « pour
  nettoyer » : cela détruirait du travail non poussé.
- `explication.txt` est un long document **en français** qui retrace des
  corrections de bugs passées dans `chabanas.py` et explique `IBodyProducer`.
  Le lire avant de toucher à ce fichier — une connaissance qui n'est pas
  ailleurs.
- `README.md` documente la stratégie de tests ; s'y référer avant d'en ajouter.

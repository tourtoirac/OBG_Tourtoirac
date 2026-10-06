# Tourtoirac

Serveur WebSocket **Twisted / autobahn** : logique de jeu et état des
composants. Python >= 3.14, Poetry.

Projet frère de `OBG/Chabanas` (API HTTP) et `OBG/Raffaillac` (frontend).
Le format de `game_json` est possédé par le client : toute modification du
protocole doit être répercutée dans les deux autres dépôts.

## Commandes

```bash
poetry install
poetry run python main.py        # démarre le WebSocket sur 0.0.0.0:9000 (en dur dans main.py)
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
| `main.py`              | `GameWebSocketProtocol`, dispatch, démarrage (`main`, `start_server`) |
| `handle_message.py`    | Dispatch : un handler par action |
| `session.py`           | **Cœur** : état de la partie, composants, diffusion |
| `Components/`          | `board`, `bag`, `card`, `counter`, `deck`, `dice`, `token`, `component` |
| `chabanas.py`          | Client HTTP vers le back-end, via `IBodyProducer` |
| `lobby.py` `user.py`   | Lobby et utilisateur connecté |
| `player_sim.py`        | Simulateur de joueur, pour essai manuel |
| `conf.py`              | **Code mort** : importé nulle part (voir § Configuration) |

### Objets en mémoire

```
Lobby  (un seul, créé dans main.main())
 ├── users    : {protocol → User}       toute connexion WS ouverte, lobby compris
 ├── sessions : {session.key → Session}  seulement les sessions ayant ≥ 1 connecté
 └── chabanas : Chabanas                 client HTTP (Agent Twisted)

Session
 ├── players / watchers : [User]
 ├── components_lists   : {"fixed", "movable", "dice"} → [Component]   ordre = ordre de dessin
 ├── components_dict    : {component_id → Component}
 └── game_json, options, setup, setup_done, owner_nickname, closed

User : id (uuid), name (= pseudo), protocol, session, role ("player"/"watcher"), acquired
```

`self` dans les handlers est le `GameWebSocketProtocol` ; on obtient l'appelant
par `self.factory.lobby.get_user(self)`.

### Trajet d'un message

1. `GameWebSocketProtocol.onMessage` (`main.py`) décode le JSON et appelle
   `handle_message`. Un `match action:` y aiguille vers le handler.
2. Le handler (`handle_message.py`) valide les champs. Pour les actions sur
   composant, le préambule commun `resolve_component_action` vérifie les
   champs, l'utilisateur, la session, le rôle `player` et l'existence du
   composant.
3. Il modifie l'objet `Component` puis diffuse par `user.session.send(...)` à
   tous (joueurs **et** spectateurs, émetteur compris), ou répond au seul
   appelant par `user.send(...)`.
4. Une erreur repart sous la forme `{"event": "error", "error": {code, message}}`
   via `self.send_error`.

Les handlers qui parlent à Chabanas sont des `@defer.inlineCallbacks` ; `main.py`
leur attache un errback de journalisation.

### Protocole : action reçue → événement émis

| Action | Événement | Destinataires |
| ------ | --------- | ------------- |
| `list_game` | `list_game` `{game_list}` | appelant |
| `list_sessions` | `sessions_info` `{content: {active}}` | appelant |
| `create_session` `join_session` `resume_session` | `session_joined` `{session, role, owner}` | appelant |
| ↳ (effet de bord) | `session_players_changed` | **tous les connectés du lobby** |
| `close_session` | `session_closed` puis `session_players_changed` | session, puis lobby |
| `acquire` / `release` | `acquire` / `release` `{component_id, user, success}` | session |
| `move` | `move` `{component_id, coordinates}` | session |
| `roll` | `roll` `{src, cooldown_seconds}` | session |
| `rotate` | `rotate` `{orientation}` | session |
| `flip` | `flip` `{side, front_src, back_src}` | session |
| `increment` / `decrement` | `counter_value` `{value}` | session |
| `fix_positions` | `fix_positions` `{components}` | session |
| `apply_setup` | `setup` `{components}` | session |
| (minuterie 30 s) | `keep_alive` | tous |
| (arrêt du réacteur) | `server_shutdown` | tous |

Le client attend aussi `session_created`, que le serveur n'émet jamais.

### Cycle de vie d'une session

- **Création / adhésion** : on interroge toujours Chabanas, qui tranche
  (`access_key`, `key`, places), puis on construit la `Session` à partir du
  `game_json` qu'il renvoie si elle n'est pas déjà en mémoire. Si elle y est,
  l'état en mémoire **prime** sur ce que Chabanas renvoie.
- **Passage lobby → jeu** : ce sont deux pages, donc deux WebSocket. La
  fermeture de la connexion du lobby peut vider la session. `lobby.delete_user`
  appelle alors `_save_state_if_empty`, qui envoie `game_json_state()` à
  `/session/update` puis retire la session de la mémoire **si elle est toujours
  vide**. La page de jeu envoie `resume_session` (avec `session_key` et
  `session_code`) ; si la session n'est plus en mémoire, `_reload_session` la
  reconstruit depuis Chabanas.
- **Clôture** (owner seulement) : `update_session_state` puis `archive_session`.
  Les joueurs deviennent spectateurs et la session reste en mémoire, marquée
  `closed`.
- L'**owner** est déduit de `players[].owner` renvoyé par Chabanas et comparé au
  **pseudo** (`user.name`), pas à la connexion.

### Composants réellement chargés

`Session.load_session_components` ne reconnaît que quatre `kind` :

| `kind` | Classe | Liste | Remarque |
| ------ | ------ | ----- | -------- |
| `board` | `Board` | `fixed` | |
| `counter` | `Counter` | `fixed` | compteur numérique ± (`min`/`max`/`color`) |
| `dice` | `Dice` | `dice` | accepté dans les trois listes ; `origin_list` mémorise d'où il vient pour la sauvegarde |
| `token` | `Token` | `movable` | uniquement s'il est déclaré dans `movable` |

`Bag`, `Card` et `Deck` existent et sont testés, mais **aucun `game_json` ne
peut les instancier** : ils sont absents du `match`. Un `kind` inconnu est
ignoré sans erreur.

Attention au vocabulaire côté Raffaillac : son `Counter` (`engine/counter.ts`)
est notre **`Token`**, et son `CounterBox` est notre **`Counter`**.

### Ajouter une action

1. Écrire le handler dans `handle_message.py` (réutiliser
   `resolve_component_action` s'il agit sur un composant).
2. L'ajouter à l'import de `main.py` **et** comme `case` dans
   `GameWebSocketProtocol.handle_message`.
3. Si le handler est asynchrone (`@defer.inlineCallbacks`), écrire
   `result = handler(...)` dans le `case`, pour que l'errback de journalisation
   soit attaché au `Deferred`.
4. Côté client : type d'événement dans `Raffaillac/src/types.ts`, branche
   dans `handleServerMessage` de `game.ts`.

Actions actuelles : `list_sessions`, `create_session`, `join_session`,
`resume_session`, `close_session`, `list_game`, `acquire`, `release`, `move`,
`roll`, `rotate`, `flip`, `fix_positions`, `apply_setup`, `increment`,
`decrement`.

## Configuration

**`conf.py` n'est importé par aucun module.** Ses valeurs (`ws_port`,
`back_api.host`, `GAMES_LIST`, `LOOPED_KEEP_ALIVE`…) n'ont aucun effet. La
configuration réelle est en dur dans le code :

| Valeur | Où | Valeur actuelle |
| ------ | -- | --------------- |
| Écoute WebSocket | `main.py`, `start_server()` | `ws://0.0.0.0:9000` |
| Période du keep-alive | `main.py`, `start_server()` | `30.0` s |
| Attente de Chabanas | `main.py`, `CHABANAS_MAX_RETRIES` / `CHABANAS_RETRY_INTERVAL` | 30 essais, 2 s |
| URL de Chabanas | `chabanas.py`, `Chabanas.__init__` | `http://obg-chabanas:80` |
| Délai HTTP | `chabanas.py`, `Chabanas.__init__` | `10.0` s |

Aucune variable d'environnement n'est lue. La liste des jeux affichés vient
du client (`Raffaillac/public/conf.json`), qui la transmet dans chaque
`list_game` et `list_sessions`.

## Démarrage

`main()` crée le lobby puis lance le réacteur, **sans écouter**. Une fois le
réacteur démarré, `Chabanas.wait_until_ready()` interroge `/game/list` (avec
une liste vide) jusqu'à recevoir un 200 :

- dès que Chabanas répond, `start_server()` ouvre le WebSocket, lance le
  keep-alive et enregistre l'arrêt propre ;
- après `CHABANAS_MAX_RETRIES` échecs, le réacteur s'arrête et le processus
  sort avec le code 1, sans avoir jamais écouté.

Un échec réseau prend jusqu'au délai HTTP (10 s) en plus de l'intervalle : avec
un hôte qui ne répond pas du tout, l'attente peut durer plusieurs minutes. Un
Tourtoirac qui « ne démarre pas » attend donc souvent Chabanas : regarder les
lignes `[CHABANAS] Waiting for ...` du journal.

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

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
| `Components/`          | `board`, `board_group`, `bag`, `card`, `counter`, `deck`, `dice`, `dice_pool`, `token`, `component`, `hex_grid` |
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
| `start_session` (owner) | `session_status` puis `session_players_changed` (`action: "started"`) | session, puis lobby |
| (adhésion, reprise, départ) | `session_status` `{started, missing_players}` | session |
| `acquire` / `release` | `acquire` / `release` `{component_id, user, success}` ; `release` porte aussi `component_json` et `bag_id` (le sac où le pion est tombé, sinon `null`) | session |
| `pick` `{component_id, x?, y?, request_id?}` | `pick` `{component_id, user, request_id, component_json}` | session |
| ↳ `acquire` refusé | `error` `session_not_started` / `players_missing` | appelant |
| `move` | `move` `{component_id, coordinates}` | session |
| `roll` | `roll` `{src, cooldown_seconds}` | session |
| `roll_pool` | `roll_pool` `{component_id, dice: [{component_id, src, cooldown_seconds}]}` | session |
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
- **Ids en double** : à la création, `lobby.create_session` refuse un
  `game_json` dont deux composants (`fixed`, `movable`, `dice` confondus)
  partagent un `id` (`session.duplicate_component_ids`). La session que
  Chabanas vient de créer est archivée, et l'appelant reçoit l'erreur
  `duplicate_component_ids`, affichée par `lobby.ts`.
  Une session déjà stockée (adhésion, reprise) n'est pas refusée :
  `_build_session` journalise seulement ses ids en double, en `ERROR`.
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
- **Démarrage** (`Session.started`, `Session.player_names`) : Chabanas démarre
  la partie quand le dernier siège est pris, ou quand l'**owner** envoie
  `start_session` (`/session/update` avec `started: true`). Ensuite, plus aucun
  nouveau joueur, et la valeur survit à la sauvegarde et à la reprise.
  `acquire` n'est permis que si la partie a commencé **et** que tous les
  sièges enregistrés (`player_names`) sont connectés comme joueurs
  (`Session.acquire_refusal`). Les spectateurs ne comptent pas. La reprise
  (`resume_session`) refuse `session_started` à un pseudo sans siège.

### Composants réellement chargés

`Session.load_session_components` ne reconnaît que sept `kind` :

| `kind` | Classe | Liste | Remarque |
| ------ | ------ | ----- | -------- |
| `board` | `Board` | `fixed` | |
| `board_group` | `BoardGroup` | `fixed` | 1 à n `board` retournés ensemble (voir § Groupe de plateaux) |
| `counter` | `Counter` | `fixed` | compteur numérique ± (`min`/`max`/`color`) |
| `dice` | `Dice` | `dice` | accepté dans les trois listes ; `origin_list` mémorise d'où il vient pour la sauvegarde |
| `dice_pool` | `DicePool` | `fixed` | 1 à n `dice` lancés ensemble (voir § Groupe de dés) ; uniquement s'il est déclaré dans `fixed` |
| `bag` | `Bag` | `fixed` | sac de pions tirés au hasard (voir § Sac) ; uniquement s'il est déclaré dans `fixed` |
| `token` | `Token` | `movable` | uniquement s'il est déclaré dans `movable` (ou dans un `bag`) |

`Card` et `Deck` existent et sont testés, mais **aucun `game_json` ne
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
`resume_session`, `close_session`, `start_session`, `list_game`, `acquire`, `release`, `pick`, `move`,
`roll`, `roll_pool`, `rotate`, `flip`, `fix_positions`, `apply_setup`, `increment`,
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
de Chabanas (jeux `active`) : `list_game` et `list_sessions` n'attendent aucun
`game_name_list`, et Tourtoirac n'en envoie pas à `/game/list` ni à
`/session/list`.

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

## Copies d'un pion (`copies`)

Un `token` déclaré avec `"copies": n` dans un `game_json` de jeu est remplacé,
à la construction de la session, par n pions identiques dont l'`id` est celui
du pion déclaré suivi de `-01`, `-02`… (deux chiffres au moins, trois à partir
de 100 copies). **Le pion déclaré n'est pas créé** : son `id` n'existe pas dans
`components_dict`, et une entrée de `setup` qui le nomme est ignorée — viser
`prep-fire-03`, pas `prep-fire`.

- `session.expand_copies` / `expand_components`, appelés par
  `load_session_components` et par `duplicate_component_ids` (un `id` de copie
  qui heurte un `id` déclaré ailleurs fait refuser le jeu).
- `copies` n'est **jamais sauvegardé** : `Token.return_json()` ne le renvoie
  pas, l'état stocké contient les n pions ordinaires, et une reprise ne les
  multiplie pas une seconde fois.
- Un `copies` qui n'est pas un entier ≥ 1 (`0`, `"20"`, `true`…) est ignoré :
  le pion est créé seul, sous son `id`.
- Seuls les `token` sont copiés. Raffaillac ne voit jamais la clé : il reçoit
  les copies comme des pions ordinaires.

## Grille hexagonale d'un plateau

Un `board` peut porter une clé optionnelle `grid`. Quand le **`board_group`**
qui le contient a `"magnetism": true`, au `release`, le centre du pion lâché est
aimanté sur le centre de l'hexagone le plus proche. Sans `magnetism` sur le
groupe (ou s'il ne vaut pas le booléen `true`), et pour un plateau seul, la
grille n'aimante rien : elle ne sert qu'à l'affichage de calibrage (touche G de
Raffaillac). Un `board` n'a plus de clé `magnetism` (ni lue, ni sauvegardée) :
`Board.snap_point` lit `board.group.magnetism`.

```json
"grid": {"type": "hex", "orientation": "flat", "origin_x": 112.5,
         "origin_y": 98.0, "size": 64.3, "size_y": 63.8, "snap_radius": 40}
```

- Coordonnées **relatives au coin haut-gauche du plateau**, en unités du
  `game_json` (`width`/`height` du plateau), pas en pixels de l'image.
  `orientation` : `flat` (défaut) ou `pointy`. `size` = rayon centre → coin.
  `size_y` (défaut `size`) et `snap_radius` (défaut : pas de limite) sont
  facultatifs.
- Calcul dans `Components/hex_grid.py` (`HexGrid`), appelé par
  `Session.snap_to_grid` depuis le handler `release`. Seul compte le plateau
  **le plus haut** sous le centre du pion ; s'il n'a pas de grille, pas
  d'aimantage. Seuls les `token` sont aimantés.
- Le retour sur la case de départ (`near_initial_position`) **prime** sur la
  grille.
- Une grille mal écrite est ignorée (`None`), sans erreur.
- `Board.return_json()` renvoie `grid` : la grille survit à la sauvegarde.
- Raffaillac refait le même calcul (`src/engine/hex_grid.ts`) pour
  l'aperçu : toute modification de l'un doit être reportée dans l'autre.

## Groupe de plateaux (`board_group`)

Un `board_group` regroupe 1 à n plateaux qui forment une seule carte. Avec
`flippable`, Raffaillac les retourne **tous ensemble**, d'un demi-tour autour du
centre du rectangle englobant du groupe : les plateaux échangent leurs places
comme le ferait une carte d'un seul tenant, et tout ce qui est posé dessus
(pions, fantômes, compteurs, dés, grille) suit. Le retournement reste local au
joueur : le serveur ne retourne rien.

**Un `board` seul ne se retourne pas et n'aimante pas** : il n'a plus de clés
`flippable` ni `magnetism` (ni lues, ni sauvegardées). Pour qu'une carte se
retourne ou aimante les pions, il faut l'envelopper dans un `board_group`, même
avec un seul plateau. `magnetism` vaut pour les grilles de tous les plateaux du
groupe.

```json
{"kind": "board_group", "id": "map", "flippable": true, "magnetism": true,
 "boards": [{"kind": "board", "id": "north", ...}, {"kind": "board", "id": "south", ...}]}
```

- Chaque plateau du groupe est un `Board` ordinaire, indexé par **son** `id`
  dans `components_dict` : le setup le déplace et sa grille aimante les pions si le groupe a
  `magnetism`. `BoardGroup` pose `board.group` sur chacun de ses plateaux.
  `Session.boards()` aplatit les groupes dans l'ordre de dessin (utilisé par
  `snap_to_grid`).
- Le groupe lui-même **n'est pas** dans `components_dict` : aucune action ne le
  vise, et une entrée de setup qui le nomme est ignorée.
- Un groupe sans aucun `board` est ignoré. Tout autre `kind` à l'intérieur est
  ignoré.
- `duplicate_component_ids` compte aussi les `id` des plateaux d'un groupe.

## Groupe de dés (`dice_pool`)

Un `dice_pool` regroupe 1 à n dés qui se lancent ensemble. Il n'a ni image ni
position : c'est un composant `fixed` transparent, qui dit seulement quels dés
vont ensemble. Raffaillac dessine un bouton « roll » au-dessus de ses dés.

```json
{"kind": "dice_pool", "id": "combat", "dice": [
  {"kind": "dice", "id": "red", "x": 100, "y": 100, "width": 90, "height": 90, "src_list": [...]},
  {"kind": "dice", "id": "white", "x": 200, "y": 100, "width": 90, "height": 90, "src_list": [...]}]}
```

- Chaque dé du groupe est un `Dice` ordinaire, indexé par **son** `id` dans
  `components_dict` : un clic le lance seul (`roll`) et le setup le déplace.
  Il **n'est pas** dans `components_lists['dice']` : il vit dans son groupe,
  qui le porte dans `fixed`, à l'écran comme dans l'état sauvegardé.
- Le groupe **est** dans `components_dict` : l'action `roll_pool` le vise.
- `roll_pool` lance **tout ou rien** : tant qu'un dé du groupe est dans son
  délai, l'action est refusée (`dice_cooling_down`) et aucun dé ne bouge. Un
  seul événement `roll_pool` porte toutes les faces tirées.
- Un groupe sans aucun `dice` est ignoré, comme un groupe déclaré ailleurs que
  dans `fixed`. Tout autre `kind` à l'intérieur est ignoré.
- `duplicate_component_ids` compte l'`id` du groupe et ceux de ses dés.

## Sac (`bag`)

Un `bag` est un composant `fixed` qui contient des pions hors de vue. Un pion
relâché dessus rejoint son contenu ; un clic en fait sortir un **au hasard**,
directement dans la main du joueur.

```json
{"kind": "bag", "id": "chits", "x": 100, "y": 100, "width": 120, "height": 120,
 "src": "/Games/Tools/Bag/bag.png", "components": [
  {"kind": "token", "id": "chit-a", "x": null, "y": null, "width": 40, "height": 40,
   "front_src": "...", "back_src": null}]}
```

- Chaque pion du sac est un `Token` ordinaire, indexé par **son** `id` dans
  `components_dict`, mais il **n'est pas** dans `components_lists['movable']`
  et ses `x`/`y` valent `None` tant qu'il n'est pas sorti (`Token.leave_table`
  / `enter_table`). Le sac le porte dans `fixed`, à l'écran comme dans l'état
  sauvegardé.
- `pick` (`Session.pick_from_bag`) : mêmes conditions qu'`acquire`
  (`acquire_refusal`). Le pion revient en fin de `movable`, centré sur le
  point `x`/`y` envoyé par le client (à défaut, le centre du sac), et il est
  acquis par l'appelant. Erreurs : `component_not_a_bag`, `bag_empty`.
  `request_id` est renvoyé tel quel : deux joueurs peuvent tirer en même
  temps, chaque client y reconnaît son pion.
- Dépôt : dans `release`, `Session.bag_at` cherche le sac sous le **centre**
  du pion ; il **prime** sur la grille et sur la case de départ. Le pion
  quitte `movable` (`put_in_bag`) et l'événement `release` porte `bag_id`.
- Un pion sorti d'un sac **n'a pas de case de départ** (`initial_x`/`initial_y`
  à `None`, pas de rectangle vert) jusqu'au prochain « Fixe la position ».
- `acquire` sur un pion encore dans un sac est refusé (`component_in_bag`).
- Le setup déplace le sac ; sur un pion encore dans le sac, il ignore `x`/`y`
  mais applique `side` (la face que le pion montrera en sortant).
- `src` et `components` sont facultatifs : un sac peut être vide et se remplir
  en cours de partie. Seuls les `token` y entrent (`copies` y est honoré) ;
  tout autre `kind` est ignoré, comme un sac déclaré ailleurs que dans `fixed`.
- `duplicate_component_ids` compte l'`id` du sac et ceux de ses pions.

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

- Commentaires et docstrings **en anglais**, comme dans les autres dépôts.
  Une grande partie du code existant est encore commentée en français : écrire
  en anglais tout commentaire ajouté ou modifié, sans traduire en masse le
  reste (cela noierait le diff).
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

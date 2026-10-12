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

## État de la suite (vérifié le 12/10/2026)

```
573 passed, 2 failed, 6 skipped
```

Les 2 échecs sont **préexistants et hors sujet** : `tests/test_chabanas.py`.
Ne pas les « corriger » au passage dans
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
   via `self.send_error`, dont le troisième argument facultatif ajoute des
   champs au bloc `error` (utilisé par `invalid_component`).

Les handlers qui parlent à Chabanas sont des `@defer.inlineCallbacks` ; `main.py`
leur attache un errback de journalisation.

### Protocole : action reçue → événement émis

| Action | Événement | Destinataires |
| ------ | --------- | ------------- |
| `list_game` | `list_game` `{game_list}` | appelant |
| `list_sessions` | `sessions_info` `{content: {active}}` | appelant |
| `create_session` `join_session` `resume_session` | `session_joined` `{session, role, owner, nationality}` | appelant |
| ↳ (effet de bord) | `session_players_changed` | **tous les connectés du lobby** |
| `close_session` | `session_closed` puis `session_players_changed` | session, puis lobby |
| `start_session` (owner) | `session_status` puis `session_players_changed` (`action: "started"`) | session, puis lobby |
| (adhésion, reprise, départ) | `session_status` `{started, missing_players}` | session |
| `acquire` / `release` | `acquire` / `release` `{component_id, user, success}` ; `release` porte aussi `component_json` et `bag_id` (le sac où le pion est tombé, sinon `null`) | session |
| `pick` `{component_id, x?, y?, request_id?}` | `pick` `{component_id, user, request_id, component_json}` | session |
| `draw` `{component_id, pile?, x?, y?, request_id?}` | `draw` `{component_id, pile, user, request_id, component_json, deck_json}` | session |
| `shuffle` `{component_id, pile?}` | `shuffle` `{component_id, pile, user, nickname, deck_json}` | session |
| `refill` `{component_id}` | `refill` `{component_id, user, nickname, deck_json}` | session |
| ↳ `acquire` refusé | `error` `session_not_started` / `players_missing` | appelant |
| `move` | `move` `{component_id, coordinates}` | session |
| `roll` | `roll` `{src, cooldown_seconds}` | session |
| `roll_pool` | `roll_pool` `{component_id, dice: [{component_id, src, cooldown_seconds}]}` | session |
| `rotate` | `rotate` `{orientation}` | session |
| `flip` | `flip` `{side, front_src, back_src}` | session |
| `increment` / `decrement` | `counter_value` `{value}` | session |
| `fix_positions` | `fix_positions` `{components}` | session |
| `apply_setup` | `setup` `{components}` | session |
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
- **Composant mal décrit** : un champ obligatoire absent (`height`, `src`…)
  ou une description qui n'est pas un objet fait lever
  `session.InvalidComponentError` pendant `load_session_components`. Elle
  nomme le composant (`id`, `kind`, liste, conteneur `bag` / `board_group` /
  `dice_pool`) et le champ manquant. `main.log_handler_error` la renvoie à
  l'appelant en erreur `invalid_component`, dont le bloc `error` porte en plus
  `component_id`, `kind`, `list`, `parent_id`, `missing_field`. À la création,
  la session que Chabanas vient de créer est archivée ; à l'adhésion et à la
  reprise, la session stockée reste telle quelle. Les autres exceptions d'un
  handler ne sont toujours que journalisées.
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

`Session.load_session_components` ne reconnaît que neuf `kind` :

| `kind` | Classe | Liste | Remarque |
| ------ | ------ | ----- | -------- |
| `board` | `Board` | `fixed` | |
| `board_group` | `BoardGroup` | `fixed` | 1 à n `board` retournés ensemble (voir § Groupe de plateaux) |
| `counter` | `Counter` | `fixed` | compteur numérique ± (`min`/`max`/`color`) |
| `dice` | `Dice` | `dice` | accepté dans les trois listes ; `origin_list` mémorise d'où il vient pour la sauvegarde |
| `dice_pool` | `DicePool` | `fixed` | 1 à n `dice` lancés ensemble (voir § Groupe de dés) ; uniquement s'il est déclaré dans `fixed` |
| `bag` | `Bag` | `fixed` | sac de pions tirés au hasard (voir § Sac) ; uniquement s'il est déclaré dans `fixed` |
| `token` | `Token` | `movable` | uniquement s'il est déclaré dans `movable` (ou dans un `bag`) |
| `deck` | `Deck` | `fixed` | paquet de cartes `single` ou `duo` (voir § Paquet de cartes) ; uniquement s'il est déclaré dans `fixed` |
| `card` | `Card` | `movable` | carte, sous-classe de `Token` ; uniquement dans `movable` (ou dans un `deck`) |

Un `kind` inconnu est ignoré sans erreur.

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
`draw`, `shuffle`, `refill`,
`roll`, `roll_pool`, `rotate`, `flip`, `fix_positions`, `apply_setup`, `increment`,
`decrement`.

## Configuration

**`conf.py` n'est importé par aucun module.** Ses valeurs (`ws_port`,
`back_api.host`, `GAMES_LIST`, `LOOPED_KEEP_ALIVE`…) n'ont aucun effet. La
configuration réelle est en dur dans le code :

| Valeur | Où | Valeur actuelle |
| ------ | -- | --------------- |
| Écoute WebSocket | `main.py`, `start_server()` | `ws://0.0.0.0:9000` |
| Ping WebSocket | `main.py`, `WS_PING_INTERVAL` / `WS_PING_TIMEOUT` | ping toutes les 30 s, pong attendu 10 s |
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

- dès que Chabanas répond, `start_server()` ouvre le WebSocket et enregistre
  l'arrêt propre ;
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

## Nationalités (`nationalities`)

Notion **facultative** : un `game_json` peut déclarer, **dans ses `options`**,
`"nationalities": ["US", "NVA"]` (la clé n'est pas lue à la racine), et un `token` peut porter
`"nationality": "US"`.

**Seule règle pour l'instant** : un pion qui porte une nationalité n'est touché
que par un joueur de cette nationalité (`Component.acquirable_by`, redéfini par
`Token`). `acquire`, `rotate` et `flip` répondent sinon par l'erreur
`wrong_nationality`, sans rien diffuser. `pick` ne tire que parmi les pions du sac que le joueur
peut prendre (`Bag.pick(user)`) ; s'il n'en reste que d'autres nationalités,
même erreur. Un pion sans nationalité reste à tout le monde ; un joueur sans
nationalité ne prend aucun pion qui en a une.

### Une règle d'acquisition vaut pour toutes les modifications

`handle_message.refuse_modification(self, user, component)` est **le seul
endroit** qui dit si un joueur peut toucher un composant. `acquire`, `rotate`
et `flip` l'appellent tous les trois : partie non démarrée
(`session_not_started`), joueur absent (`players_missing`), pion dans un sac
(`component_in_bag`), nationalité (`wrong_nationality`). **Toute nouvelle règle
qui empêche un `acquire` s'ajoute là**, jamais dans un seul handler, pour
qu'elle interdise aussi la rotation et le retournement. Une future action de
modification doit l'appeler aussi. Côté client, le miroir est
`modificationRefused` dans `Raffaillac/src/game.ts`.

`move` et `release` n'y passent pas : ils exigent déjà de tenir le pion, donc
de l'avoir acquis. `pick` applique les mêmes règles à sa façon (règles de
session, puis tirage parmi les pions que le joueur peut prendre).

- Le joueur choisit sa nationalité en créant ou en rejoignant la partie
  (champ `nationality` de `create_session` / `join_session`). **Chabanas est
  seul juge** : il exige une valeur de la liste pour tout nouveau siège (422 →
  erreur `invalid_nationality`) et la stocke sur le siège. Plusieurs joueurs
  peuvent partager la même.
- `Session.player_nationalities` (`{pseudo: nationalité}`) est relu depuis la
  liste `players` de Chabanas (`Lobby._seat_nationalities`), à la construction
  et à chaque adhésion (`refresh_seats`). `Session.add_user` pose
  `user.nationality` à partir du **pseudo** : le siège porte la nationalité,
  pas la connexion. Un spectateur a `None`.
- `return_session_json` renvoie `nationalities` et `player_nationalities` ;
  `session_joined` renvoie celle de l'appelant.
- `Token.nationality` (`None` si absente ou mal écrite) est renvoyée par
  `return_json` : elle survit à la sauvegarde. `session.read_nationalities`
  fait la même lecture que `Chabanas/Game/tools.py`.
- `Chabanas.create_session` renvoie désormais `(session_info, reason)`, comme
  `join_session`.

### Face cachée : elle ne quitte pas le serveur

La face qu'un pion **à nationalité** ne montre pas (`front_src` s'il est sur
son dos, `back_src` sinon) n'est envoyée qu'aux **joueurs de cette
nationalité**. Les autres joueurs, un joueur sans nationalité et tous les
spectateurs reçoivent `null` à la place (`Session.sees_hidden_face`).

- `Session.for_user(payload, user)` fait le tri : il parcourt le message et
  retire la face cachée de **toute** description de pion, reconnue à ses clés
  `front_src` + `back_src` (un `return_json`, une liste, l'événement `flip`).
- `Session.send` / `send_others` l'appliquent à **chaque destinataire** :
  un message diffusé ne peut pas fuiter par oubli. `return_session_json(user)`
  fait de même pour `session_joined`. **Ne jamais envoyer un `return_json` de
  pion par `user.send` sans passer par `for_user`.**
- `game_json_state()` (l'état stocké dans Chabanas) garde les deux faces.
- Un pion que le setup va retourner est décrit, avant le setup, encore sur sa
  face du `game_json` : c'est la face que le setup cachera qui est retenue
  (`_pending_sides`), pour qu'elle ne parte pas avec la description initiale.
- Sans aucun pion à nationalité (`Session.hides_faces` faux), les messages
  partent tels quels, sans parcours.
- Limite : l'`id` du pion part toujours. S'il dit ce qu'est le pion
  (`us_marines-17`), utiliser `options.anonymous_id` (ci-dessous).

## Liste des participants (`participants`)

`Session.participants()` renvoie `[{nickname, role, connected}]` : les sièges
(`player_names`, puis les joueurs à la table sans siège connu) avec
`connected`, puis les spectateurs. Elle part dans `return_session_json` et
dans chaque `session_status`, donc à chaque arrivée ou départ ; la clôture
renvoie aussi un `session_status`, les joueurs y gardant leur ligne de joueur.

## Documents (`documents`)

Clé **facultative, à la racine** du `game_json` (pas dans `options`) :
`"documents": [{"src": "...", "name": "..."}]`, des PDF que les joueurs
consultent depuis la page de jeu. `session.read_documents` nettoie la liste
(entrée sans `src` écartée, `name` absent remplacé par le `src`),
`return_session_json` la renvoie dans `documents`, et `game_json_state` la
conserve puisqu'elle reste dans le `game_json`.

## Ids anonymes (`options.anonymous_id`)

Avec `"options": {"anonymous_id": true}`, `session.anonymize_ids` remplace
l'`id` de chaque `token` (ceux de `movable` et ceux des `bag`, copies
comprises) par un uuid tiré au hasard, **une seule fois**, à la première
construction de la session. Les entrées de `setup` qui nomment un pion suivent
le nouvel id. Les options reçoivent alors `"anonymized": true` : c'est ce
marqueur, stocké avec la session, qui empêche une reprise de tirer de nouveaux
ids. Les nouveaux ids sont ceux de l'état sauvegardé (`game_json_state`).

- Seule la valeur booléenne `true` compte. Plateaux, compteurs, dés et sacs
  gardent leur id.
- Le `game_json` reçu de Chabanas n'est pas modifié : la session travaille sur
  une copie, que Chabanas ne reçoit qu'à la première sauvegarde. Une session
  perdue avant d'avoir été sauvegardée repart donc avec d'autres ids.
- `duplicate_component_ids` contrôle toujours les ids **déclarés** par le jeu.

`options` est recopié tel quel vers les clients : `component_id_display`
(`false` masque le bandeau qui affiche le nom et la position d'un pion) n'est
lu que par Raffaillac, Tourtoirac n'en fait rien.

`options.fix_positions` est un booléen : `false` désactive « Fixe la
position » (Raffaillac masque son bouton, et l'action `fix_positions` est
refusée avec `fix_positions_disabled`) ; absente ou `true`, elle reste permise.
Une partie sauvegardée avant que l'option devienne un booléen peut encore
porter `null` (désactivé) ou une position `{x, y}` (permis).
`options.screen_capture` (`null` ou `false` masque le bouton « Copie d'écran »)
n'est lu que par Raffaillac.

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

- `count` (facultatif) : seul `true` fait afficher par le client la pastille
  du nombre de pions restants ; absent ou autre valeur, elle n'est pas
  dessinée (`Bag.count`, renvoyé par `return_json`).
- `text` (facultatif) : légende que le client écrit **sous** le sac ; absent,
  `null` ou vide, rien n'est écrit. Le serveur ne fait que le transporter
  (`Bag.text`, renvoyé par `return_json`, donc conservé dans l'état sauvegardé).
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

## Main privée (`hands`)

**Active seulement si `options.private_hand` vaut `true`**
(`Session.private_hand_enabled`). Sinon un `release` avec `"hand": true` pose
le pion là où il est relâché, et `take` répond `private_hand_disabled`.

Chaque joueur garde hors de la table des pions que lui seul connaît.
`Session.hands` est un `{pseudo: [Token]}` : la main appartient au **siège**
(le pseudo), pas à la connexion, et survit donc à une déconnexion.

- Dépôt : `release` avec `"hand": true` (`Session.put_in_hand`). Le pion doit
  être tenu par l'appelant ; il quitte `movable` et perd sa position
  (`leave_table`), comme dans un sac. Seuls un `token` et une `card` y entrent. L'événement
  `release` porte `hand_of` (pseudo, `None` sinon) et `hands`.
- Reprise : action `take` `{component_id, x, y, request_id}`
  (`Session.take_from_hand`), mêmes conditions qu'`acquire`
  (`acquire_refusal`). Le pion revient en fin de `movable`, centré sur
  `x`/`y`, acquis par l'appelant. Événement `take` (`hand_of`, `hands`,
  `request_id`, `component_json`). Erreur : `component_not_in_hand`.
- `acquire`, `flip` et `rotate` sur un pion en main sont refusés
  (`component_in_hand`, dans `refuse_modification`).
- Ce que tout le monde lit : `hands_json()` → `{pseudo: [{src, width,
  height}]}`, le **dos** de chaque pion, sans `id` ni face. `src` vaut `None`
  pour un pion sans dos ou qui porte une nationalité. Il part dans
  `return_session_json` (`hands`) et dans chaque `release` / `take`.
- Ce que le détenteur lit : `return_session_json(user)["hand"]`, ses pions
  complets (`hand_json`) ; vide pour un spectateur.
- `game_json_state` écrit `hands` à la racine quand il y en a ; `load_hands`
  le relit à la reprise. Un `game_json` de jeu n'en déclare pas.
- Le setup ignore `x`/`y` d'un pion en main, comme pour un sac.

## Paquet de cartes (`deck`) et carte (`card`)

Un `deck` est un composant `fixed`, **immobile**, qui ne contient que des
`card`. Deux types : `single` (une pile, `draw`) et `duo` (une pioche `draw`
et une défausse `discard`). Tout autre `type` vaut `single`.

```json
{"kind": "deck", "id": "actions", "type": "duo",
 "x": 100, "y": 100, "width": 243, "height": 385,
 "back_src": "/cards/back.png", "text": "Actions",
 "discard_x": 400, "discard_y": 100,
 "components": [{"kind": "card", "id": "action_01", "front_src": "/cards/action_01.png"}]}
```

- `width`/`height` sont ceux **d'une pile**, donc d'une carte. La défausse est
  à droite de la pioche (`DECK_GAP` = 20 px) sauf `discard_x`/`discard_y`.
- `components` = la pioche, **dernière carte au-dessus**. `discard` = la
  défausse : un jeu la déclare rarement, l'état sauvegardé toujours.
- Une carte d'un paquet n'a pas de position ; elle prend `width`, `height` et
  `back_src` du paquet si elle n'en déclare pas. `copies` fonctionne comme
  pour un pion, `options.anonymous_id` renomme aussi les cartes.
- `Card` (`Components/card.py`) est un `Token` de `kind` `card`, sans case de
  départ (`move_border` faux) : déplacement, `flip`, main privée et
  nationalité viennent de `Token`. `MOVABLE_KINDS` (`session.py`) liste les
  deux. Une carte ne va pas dans un `bag`, un pion ne va pas dans un `deck`,
  et une carte ne s'aimante pas sur une grille.
- Entrée : `release` d'une carte dont le **centre** est sur une pile
  (`Session.deck_at` → `put_in_deck`). Elle va **au-dessus**, face cachée dans
  la pioche, face visible dans la défausse (`PILE_SIDES`). L'événement
  `release` porte `deck_id`, `pile` et `deck_json`.
- Sortie : action `draw` (`Session.draw_from_deck`), la carte du dessus de la
  pile `pile` (`draw` par défaut), acquise par l'appelant et centrée sur
  `x`/`y`, sur la face qu'elle avait dans la pile. Erreurs : `pile_empty`,
  `unknown_pile`, `component_not_a_deck`, `wrong_nationality`.
- `shuffle` mélange une pile ; `refill` remet la défausse **sous** la pioche,
  face cachée, **sans mélanger**. `resolve_deck_action` est leur préambule
  commun (joueur, `acquire_refusal`, `deck`, pile connue).
- `acquire`, `flip`, `rotate` sur une carte d'un paquet : `component_in_deck`
  (`refuse_modification`).
- **Les cartes ne quittent pas le serveur** : `Deck.return_json()` ne donne
  que `piles` → `{x, y, count, top: {src, width, height}}` (le dos pour la
  pioche, la face pour la défausse). `Deck.state_json()` écrit les cartes
  pour `game_json_state` ; `Component.state_json()` vaut `return_json()`
  ailleurs. Le client n'ayant pas les cartes, **chaque événement qui change
  une pile porte `deck_json`**.
- **Face d'une carte** : `sees_hidden_face` ne donne le `front_src` d'une
  carte posée sur son dos **à personne**, sauf au joueur qui la garde en main
  privée. Une carte piochée arrive donc inconnue de tous, y compris de celui
  qui la pioche, jusqu'à ce qu'il la retourne (tout le monde la voit) ou la
  mette dans sa main (lui seul). `hides_faces` est vrai dès qu'il y a une
  carte.
- Limite : l'`id` de la carte part avec elle. Un client modifié peut donc
  reconnaître une carte déjà vue, même après un mélange.

## Joueurs simulés (`bots/`, `sherlock_bot.py`)

`bots/` contient des **clients** : ils parlent le protocole WebSocket comme un
navigateur et n'importent rien du serveur. `sherlock_bot.py` (à côté de
`player_sim.py`) en lance plusieurs sur une table de Sherlock Holmes :

```bash
python3 sherlock_bot.py --create --bots 3         # table de bots seuls
python3 sherlock_bot.py --code ABCD1234 --bots 2  # bots rejoignant des humains
```

- `bots/client.py` — `TableClient` : un siège. `handle(message)` tient à jour
  ce que **ce siège** sait (cartes, piles, mains) ; les actions sont des
  coroutines qui envoient une requête et attendent l'événement qui y répond
  (`BotError` sur refus ou silence). Aucune règle de jeu. Le nom d'une carte
  vient du fichier de son `front_src` : une carte jamais vue n'a pas de nom,
  et une carte remise dans une pioche est oubliée.
- `bots/sherlock.py` — les règles (`legal`, `FOLLOWERS`, `POINTS`), `Referee`
  (suit le tour, la dernière carte, le lieu, les pioches dues, à partir des
  événements d'un siège), `SherlockBot` (stratégie) et `Game` (mise en place,
  boucle de jeu). Les ids de paquets `pack`, `villains`, `deal` sont ceux du
  `game_json` du jeu.
- Le serveur ne connaît ni tour ni règle : **le tour est déduit** des cartes
  posées sur la défausse et des pioches gardées en main. C'est pourquoi les
  événements `acquire`, `release`, `take`, `pick` et `draw` portent `nickname`.
- Les bots ne jouent jamais `alibi`, `thick_fog` ni `mycroft` (`NEVER_PLAYED`).
- Avec des humains, tout ce qui demande de se parler passe par la console du
  programme (`Operator`, commandes `aide`).
- Tests : `tests/test_sherlock_bot.py` fait jouer les bots contre la vraie
  `Session` et les vrais handlers, par une table en mémoire.

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

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

## État de la suite (vérifié le 13/10/2026)

```
646 passed, 2 failed, 6 skipped
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
| `Components/`          | `board`, `board_group`, `bag`, `card`, `counter`, `deck`, `dice`, `dice_pool`, `stack`, `token`, `component`, `hex_grid` |
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
| `acquire` / `release` | `acquire` / `release` `{component_id, user, success}` ; `release` porte aussi `component_json` et `bag_id` (le sac où le pion est tombé, sinon `null`), `deck_id`/`pile`/`deck_json`, `character_id`/`character_json` (le `mw_character` sous lequel la carte est passée) | session |
| `voice_join` | `voice_joined` `{peers, ice_servers}` à l'appelant, puis `voice_members` `{members}` | appelant, puis session |
| `voice_leave` | `voice_members` `{members}` | session |
| `voice_signal` `{to, data}` | `voice_signal` `{from, data}` | le seul destinataire |
| `chat_send` `{text, to?}` | `chat` `{message}` | session (général) ou les deux joueurs (privé) |
| `give` `{component_id, to}` | `give` `{component_id, nickname, to, user, hands, component_json}` | session |
| `pick` `{component_id, x?, y?, request_id?}` | `pick` `{component_id, user, request_id, component_json}` ; sur un `mw_character`, aussi `character_json` | session |
| `draw` `{component_id, pile?, count?, x?, y?, request_id?}` | `draw` `{component_id, pile, user, request_id, index, count, component_json, deck_json}`, **un événement par carte** | session |
| `shuffle` `{component_id, pile?}` | `shuffle` `{component_id, pile, user, nickname, deck_json}` | session |
| `refill` `{component_id}` | `refill` `{component_id, user, nickname, deck_json}` | session |
| `deal` `{component_id}` | `deal` `{component_id, user, nickname, deck_json, hands, cards}` ; `cards` diffère selon le destinataire | session |
| `show_open` `{to}`, `show_add` `{component_id}`, `show_remove` `{component_id}`, `show_close` | `show` `{owner, to, cards, ranks, component_ids}` ; contenu différent selon le destinataire | joueurs seulement |
| ↳ `acquire` refusé | `error` `session_not_started` / `players_missing` | appelant |
| `move` | `move` `{component_id, coordinates}` | session |
| `roll` | `roll` `{src, cooldown_seconds}` | session |
| `roll_pool` | `roll_pool` `{component_id, dice: [{component_id, src, cooldown_seconds}]}` | session |
| `rotate` | `rotate` `{orientation}` | session |
| `flip` | `flip` `{side, front_src, back_src}` | session |
| `increment` / `decrement` | `counter_value` `{value}` | session |
| `mark` `{component_id, column, index}` | `mark` `{component_id, column, index, value, nickname}` | session |
| `fix_positions` | `fix_positions` `{components}` | session |
| `order_players` `{nicknames}` (owner seulement) | `session_status` | session |
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

`Session.load_session_components` ne reconnaît que onze `kind` :

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
| `stack` | `Stack` | `fixed` | sous-classe de `Deck` à une seule pile, qui distribue (voir § Pile qui distribue) ; uniquement s'il est déclaré dans `fixed` |
| `card` | `Card` | `movable` | carte, sous-classe de `Token` ; uniquement dans `movable` (ou dans un `deck` / `stack`) |
| `mw_character` | `MwCharacter` | `fixed` | carte immobile à deux colonnes de cases (voir § Personnage) ; uniquement s'il est déclaré dans `fixed` |

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
`draw`, `shuffle`, `refill`, `deal`,
`roll`, `roll_pool`, `rotate`, `flip`, `fix_positions`, `apply_setup`, `increment`,
`decrement`, `take`, `give`, `show_open`, `show_add`, `show_remove`, `show_close`,
`order_players`, `voice_join`, `voice_leave`, `voice_signal`, `chat_send`.

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

Seules les variables du salon audio sont lues (`voice.py`, voir § Salon
audio) : `OBG_STUN_URLS`, `OBG_TURN_URLS`, `OBG_TURN_SECRET`, `OBG_TURN_TTL`. La liste des jeux affichés vient
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
`"nationality": "US"`. Un `token` peut aussi porter `"block_sight": true`
(seul le booléen `true` compte) : `Token.block_sight` est transmis tel quel au
client par `return_json`, qui s'en sert pour masquer, dans l'aperçu de pile,
les pions situés dessous aux autres nationalités et aux spectateurs. Le serveur
n'applique aucune règle dessus.

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

Ordre des joueurs : `Session.player_order`, l'ordre dans lequel les joueurs
ont été connus (`remember_player_order` : un nouveau venu passe après les
autres, et n'est jamais déplacé ensuite), sauf si l'owner l'a changé.
`ordered_seats()` l'applique à `participants()` et à `dealt_players()`.
`game_json_state` l'écrit à la racine (`player_order`), relu à la
construction de la session. L'action `order_players` `{nicknames}` est
réservée à l'owner (`not_session_owner`) ; les pseudos doivent être des
joueurs de la partie, chacun une fois (`unknown_player`) ; ceux qui ne sont
pas nommés gardent leur ordre, après les autres. Chabanas renvoie les sièges
par ordre d'arrivée (`player_set.order_by('id')`).

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
n'est lu que par Raffaillac, comme `options.border_shift` (seul `true` active
le défilement de la vue au bord de l'écran).

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
         "origin_y": 98.0, "size": 64.3, "size_y": 63.8, "snap_radius": 40,
         "area": {"x": 79, "y": 304, "width": 2186, "height": 2618}}
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
- `area` (facultatif, défaut : tout le plateau) est le rectangle du plateau où
  les hexagones sont imprimés, relatif à son coin haut-gauche. Un pion dont le
  centre est lâché en dehors n'est pas aimanté (`HexGrid.in_area`, testé avant
  `snap_radius`), et Raffaillac ne dessine la grille de calibrage que pour les
  hexagones centrés dedans. Une `area` mal écrite (largeur ou hauteur ≤ 0,
  champ manquant ou non numérique) fait ignorer **toute** la grille ; `null`
  vaut absence.
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

## Salon audio (`voice`)

Les joueurs d'une session se parlent par **WebRTC**, de navigateur à
navigateur (maillage complet : une connexion par paire). **Le son ne passe
jamais par Tourtoirac** : il ne fait que relayer les messages de mise en
relation et dire qui est dans le salon. Aucune option de jeu : le salon existe
dans toute partie ; les spectateurs n'y entrent pas (`watcher_not_allowed`).

- `Session.voice` : `[User]`, une entrée par connexion, dans l'ordre
  d'arrivée. **Jamais sauvegardé.** `voice_members()` en donne les pseudos,
  repris par `return_session_json` (`voice`).
- `voice_join` (`join_voice`) : l'appelant reçoit `voice_joined` avec
  `peers` (ceux déjà là) et `ice_servers` ; **c'est le nouveau venu qui
  appelle chacun d'eux**, les autres attendent son offre. Les actions étant
  traitées une à une, deux joueurs ne s'appellent jamais mutuellement. Une
  ancienne connexion du même pseudo est retirée du salon.
- `voice_leave` (`leave_voice`) et `Session.remove_user` (départ, coupure,
  connexion remplacée) retirent du salon et diffusent `voice_members`.
- `voice_signal` `{to, data}` : `data` est opaque (offre, réponse ou
  candidat ICE), un objet de 64 Kio au plus (`invalid_voice_signal`). Envoyé
  par `user.send` au seul destinataire, sans `Session.send`. Erreurs :
  `voice_not_joined` (l'appelant n'est pas dans le salon), `unknown_player`
  (destinataire absent du salon, ou soi-même).
- Serveurs ICE (`voice.ice_servers`) : `OBG_STUN_URLS` (par défaut
  `stun:stun.l.google.com:19302`, vide pour aucun) ; un serveur TURN seulement
  si `OBG_TURN_URLS` **et** `OBG_TURN_SECRET` sont posés. Le mot de passe TURN
  est éphémère (`turn_credentials`, schéma `use-auth-secret` de coturn :
  `"<expiration>:<pseudo>"` et HMAC-SHA1 en base64, `OBG_TURN_TTL` secondes,
  un jour par défaut). Sans TURN, l'audio échoue entre certains réseaux
  (NAT symétrique, réseaux d'entreprise, 4G). En production, le relais est
  le service `obg-coturn` : configuration et mise en service dans
  `OBG/deploy/coturn/README.md`.
- Tests : `tests/test_voice.py`.

## Chat (`chat`)

Chat texte de la session (`chat.py`, `ChatHistory`) : un **canal général**
lu et écrit par tous (joueurs et spectateurs), et un **canal privé par paire
de joueurs**, que ces deux seuls lisent. Aucune option de jeu.

- Message : `{id, from, to, text, time}` ; `to` vaut `null` sur le canal
  général, le pseudo de l'autre joueur sur un canal privé. `id` croît avec
  chaque message de la session, tous canaux confondus ; `time` en ISO 8601 UTC.
- `chat_send` `{text, to?}` : `text` est nettoyé de ses blancs, 1 à
  `MAX_TEXT_LENGTH` (500) caractères (`invalid_chat_text`). Avec `to`, seul un
  joueur écrit, à un autre siège de la partie, connecté ou non
  (`Session.chat_partner`, sinon `unknown_player`). `Session.post_chat`
  envoie l'événement `chat` à tous (général) ou aux connexions des deux
  joueurs (privé), par `user.send`, sans `for_user`.
- `return_session_json` porte `chat` : les messages que le lecteur peut lire
  (`readable_by`), du plus ancien au plus récent ; sans lecteur, le général seul.
- **Sauvegardé** dans l'état (`game_json_state`, clé `chat` :
  `{general: [...], private: [{players: [a, b], messages: [...]}]}`), écrite
  seulement si un message existe. `MAX_HISTORY` (200) messages gardés par
  canal. Un historique mal formé est ignoré message par message.
- Tests : `tests/test_chat.py`.

## Main privée (`hands`)

**Active seulement si `options.private_hand` est un objet, même vide `{}`**
(`null`, absent ou toute autre valeur, `true` compris : pas de main)
(`Session.private_hand_enabled`). Sinon un `release` avec `"hand": true` pose
le pion là où il est relâché, et `take` répond `private_hand_disabled`.

Chaque joueur garde hors de la table des pions que lui seul connaît.
`Session.hands` est un `{pseudo: [Token]}` : la main appartient au **siège**
(le pseudo), pas à la connexion, et survit donc à une déconnexion.

- Dépôt : `release` avec `"hand": true` (`Session.put_in_hand`). Le pion doit
  être tenu par l'appelant ; il quitte `movable` et perd sa position
  (`leave_table`), comme dans un sac. Seuls un `token` et une `card` y entrent. L'événement
  `release` porte `hand_of` (pseudo, `None` sinon) et `hands`. Avec
  `to_player` (pseudo), le pion va dans la main de **ce** joueur
  (`hand_of` le nomme) : n'importe quel siège sauf l'appelant
  (`receives_hand_of`, sur `dealt_players`), sinon `unknown_player` et rien
  ne bouge, le pion reste tenu.
- Don d'une main à l'autre : action `give` `{component_id, to}`
  (`Session.give_from_hand`), sans passer par la table. Mêmes conditions que
  `take` (`acquire_refusal`, `private_hand_disabled`) ; `to` suit
  `receives_hand_of` (`unknown_player`), le pion doit être dans la main de
  l'appelant (`component_not_in_hand`). Une carte montrée quitte la zone
  (`show` renvoyé). Événement `give` à toute la session : `for_user` ne laisse
  la face d'une carte qu'au receveur.
- Reprise : action `take` `{component_id, x, y, request_id}`
  (`Session.take_from_hand`), mêmes conditions qu'`acquire`
  (`acquire_refusal`). Le pion revient en fin de `movable`, centré sur
  `x`/`y`, acquis par l'appelant. Événement `take` (`hand_of`, `hands`,
  `request_id`, `component_json`). Erreur : `component_not_in_hand`.
- `acquire`, `flip` et `rotate` sur un pion en main sont refusés
  (`component_in_hand`, dans `refuse_modification`). **Exception** : le
  détenteur retourne ce qu'il garde dans **sa** main (`flip`, traité avant
  `refuse_modification`). L'événement `flip` n'est alors envoyé qu'à lui.
- `Session.enter_hand` est le seul point d'entrée dans une main (`release`,
  `give` et `deal`) : une **carte** y passe face visible (`side` `front`) ; un pion
  garde sa face. Le pion ou la carte est rejoué sur la face qu'il a en main.
- Ce que tout le monde lit : `hands_json()` → `{pseudo: [{src, width,
  height}]}`, le **dos** de chaque pion, sans `id` ni face. `src` vaut `None`
  pour un pion sans dos ou qui porte une nationalité. Il part dans
  `return_session_json` (`hands`) et dans chaque `release` / `take`.
- Ce que le détenteur lit : `return_session_json(user)["hand"]`, ses pions
  complets (`hand_json`) ; vide pour un spectateur.
- `game_json_state` écrit `hands` à la racine quand il y en a ; `load_hands`
  le relit à la reprise. Un `game_json` de jeu n'en déclare pas.
- Le setup ignore `x`/`y` d'un pion en main, comme pour un sac.

### Montrer des cartes de sa main (`shows`)

Actif seulement si `options.private_hand.show_hands_to_other_players` vaut
`true` (`Session.show_hands_enabled`) ; sinon `show_hands_disabled`.

- `Session.shows` : `{pseudo: {"to": "all" | pseudo, "cards": [composants],
  "ranks": [rangs], "next_rank": n}}`. Chaque carte reçoit son rang (1, 2, …)
  quand elle est montrée (`add_to_show`) : il ne change plus, et n'est jamais
  redonné, même si la carte sort de la zone et y revient. Seule la fermeture
  de la zone remet les rangs à 1.
  Les cartes montrées **restent dans `hands`** : elles sont seulement lues par
  plus de monde. Fermer la zone (`close_show`) suffit donc à les « rendre ».
- `shows` n'est **jamais sauvegardé** : à la reprise, toutes les cartes sont
  en main et aucune zone n'est ouverte.
- `show_open` `{to}` : `"all"` ou le pseudo d'un autre joueur
  (`dealt_players`), sinon `unknown_player`. Redemandé, il change le
  destinataire en gardant les cartes. `show_add` : `show_not_open`,
  `component_not_in_hand`. `show_remove` (`remove_from_show`) remet une
  carte montrée dans la main seule, les autres gardent leur rang ; sinon
  `component_not_shown`. `show_close` sans zone ouverte ne dit rien.
- `show_json(pseudo, user)` : chaque carte **telle quelle**, sur la face que
  son détenteur lui a laissée (`{src, width, height}`, sans id), et `ranks`
  dans le même ordre ; seul le
  détenteur reçoit `component_ids`. Qui ne lit pas la zone reçoit
  `to: null` et aucune carte (`reads_show_of` : le détenteur, le
  destinataire, ou tous les joueurs ; jamais un spectateur).
- `send_show` écrit directement à chaque joueur, **sans `Session.send`** ni
  `for_user` (les faces sont faites pour être lues) ; les spectateurs ne
  reçoivent rien.
- Retourner une carte montrée (`flip` en main) et la jouer (`take`)
  renvoient un `show` ; `take_from_hand` la retire de la zone
  (`remove_from_show`).
- `return_session_json(user)["shows"]` : les zones que `user` lit, la sienne
  d'abord.
- Tests : `tests/test_show_hands.py`.

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
- `draw` avec `count` (`Session.draw_cards_from_deck`) sort plusieurs cartes
  du dessus, `DRAW_MAX` (10) au plus, ou plus si le paquet a un bouton de
  pioche plus grand (`card_pickup_quantity`) ; une valeur qui n'est pas un entier ≥ 1
  vaut 1. Une pile trop courte donne ce qu'elle a. Le serveur envoie **un
  événement `draw` par carte**, avec le même `request_id`, `index` (0 à
  `count` − 1) et `count` (le nombre réellement sorti) ; `deck_json` y est
  le paquet une fois toutes les cartes sorties. Les cartes arrivent en
  éventail, décalées de `DRAW_FAN` (20 px) vers la droite.
- `shuffle` mélange une pile ; `refill` remet la défausse **sous** la pioche,
  face cachée, **sans mélanger**. `resolve_deck_action` est leur préambule
  commun (joueur, `acquire_refusal`, `deck`, pile connue).
- `card_pickup_quantity` (facultatif, `deck` et `stack`) : les boutons de
  pioche multiple que le client dessine sur chaque pile, un par entier.
  `read_pickup_quantities` (`Components/deck.py`) la nettoie : absente ou ni
  `false` ni liste → `[2, 5, 10]` (`DEFAULT_PICKUP_QUANTITIES`) ; `false` →
  `[]`, aucun bouton ; une liste → ses entiers ≥ 1, sans doublon, dans l'ordre,
  5 au plus (`PICKUP_QUANTITIES_MAX`), le reste ignoré sans erreur. La liste
  nettoyée part dans `return_json` (donc dans la sauvegarde, où `[]` reste
  `[]`).
- `orientation` (facultatif, `deck` et `stack`) : angle des piles autour de
  leur centre, 0, 90, 180 ou 270 (`read_deck_orientation` ; autre valeur → 0).
  `x`/`y`/`width`/`height` restent ceux de la pile non tournée, comme pour un
  pion ; `Deck.footprint(pile)` donne le rectangle couvert (largeur et hauteur
  échangées pour un quart de tour), que lit `pile_at` pour le dépôt d'une
  carte. Une carte piochée (deck ou stack, une ou plusieurs) sort **droite**,
  `orientation` 0 (`draw_from_deck` → `Card.tap(0)`). Envoyée et sauvegardée par `return_json`.
- **Rotation des cartes** : optionnelle, comme pour un pion. Seule une carte
  déclarant `"orientable": true` tourne (absente ou `false` →
  `component_not_rotatable`). `rotate` la tourne de 45° et diffuse
  l'événement `rotate` à toute la session ; mêmes refus qu'un pion
  (`refuse_modification`, `component_held`). L'angle et `orientable` sont
  sauvegardés. Une carte qui entre dans une main privée repasse à
  `orientation` 0 (`Session.enter_hand`, donc aussi `deal` et `give`) ; une
  carte relâchée sur un deck ou un stack prend l'`orientation` de celui-ci
  (`Session.put_in_deck`).
- `acquire`, `flip`, `rotate` sur une carte d'un paquet : `component_in_deck`
  (`refuse_modification`).
- `display_shuffle` (facultatif, `deck` et `stack`) : seul `false` masque
  les boutons Shuffle (`Deck.display_shuffle`, envoyé et sauvegardé par
  `return_json`) ; l'action `shuffle` répond alors `shuffle_disabled`.
  `shuffle_at_startup` n'est pas concerné.
- `shuffle_at_startup` (facultatif, `deck` et `stack`) : seul le booléen
  `true` fait mélanger la **pioche** à la construction du paquet
  (`Session.build_deck`), donc au lancement de la partie. La clé n'est
  **jamais sauvegardée** (`state_json` ne l'écrit pas, comme `copies`) : une
  reprise garde l'ordre des cartes. Une session perdue avant sa première
  sauvegarde est mélangée à nouveau. La défausse n'est pas mélangée, et le
  client ne reçoit pas la clé.
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
- **Carte en main** : elle y est face visible, mais `for_user` retire son
  `front_src` pour tout autre que son détenteur (`Session.reads_hand_of`),
  quelle que soit sa face. Posée depuis la main (`take`), elle arrive sur la
  table face visible, sauf si son détenteur l'a retournée en main.
- Limite : l'`id` de la carte part avec elle. Un client modifié peut donc
  reconnaître une carte déjà vue, même après un mélange.

## Pile qui distribue (`stack`)

Un `stack` (`Components/stack.py`) est une sous-classe de `Deck` à **une seule
pile** (`draw`) : tout ce qui vaut pour un `deck` `single` vaut pour lui
(immobile, cartes seulement, entrée face cachée au-dessus, `draw`, `shuffle`,
`component_in_deck`, cartes gardées sur le serveur, `deck_json`). Les tests
`isinstance(x, Deck)` de `session.py` et `resolve_deck_action` le couvrent ;
`DECK_KINDS` liste les deux `kind` pour les lectures du `game_json` brut.

```json
{"kind": "stack", "id": "pack", "x": 100, "y": 100, "width": 300, "height": 450,
 "back_src": "/cards/back.png", "text": "Pioche",
 "components": [{"kind": "card", "id": "c01", "width": 243, "height": 385, "front_src": "/cards/c01.png"}]}
```

- `width`/`height` sont ceux de la **zone** de dépôt, pas d'une carte : une
  carte y entre dès que son centre est dedans, et le client la dessine
  **centrée** dans la zone, à sa propre taille (`piles.draw.top.width/height`).
  Une carte qui ne déclare pas sa taille prend celle de la zone.
- `type`, `discard`, `discard_x/y` sont sans effet : pas de défausse, `refill`
  répond `unknown_pile`. Des cartes listées dans `discard` rejoignent la pile.
- `deal` (facultatif, booléen) : seul `true` fait distribuer le stack
  (`Stack.deal_enabled`, envoyé et sauvegardé sous `deal` par
  `return_json`) ; absent ou `false`, pas de bouton Deal et l'action `deal`
  répond `deal_disabled`. Le bot Sherlock (`is_stack`) ne distribue que par
  un stack qui le déclare, sinon il pioche.
- `text` (deck et stack) : légende ; absente, `null`, vide ou non textuelle,
  elle vaut `None` (`null` envoyé) et rien n'est écrit.
- `deal` (`Session.deal_from_stack`) donne **une carte à chaque joueur**, du
  dessus vers le dessous, dans l'ordre des sièges (`Session.dealt_players` :
  `player_names`, puis les joueurs à la table sans siège connu). Les cartes
  vont dans les **mains privées**, sans passer par la table, face visible
  pour leur détenteur seul (`enter_hand`).
  C'est tout ou rien : sans une carte par joueur, rien ne bouge.
- Erreurs de `deal` : celles de `resolve_deck_action`, puis
  `component_not_a_stack`, `deal_disabled`, `private_hand_disabled`
  (`options.private_hand` n'est pas un objet), `not_enough_cards`.
- L'événement `deal` n'est **pas** envoyé par `Session.send` : chaque
  destinataire reçoit son propre `cards` (la carte qu'il a reçue, avec sa
  face ; liste vide pour un spectateur), passé par `for_user`. Les cartes des
  autres ne partent pas, pas même leur `id`.
- `deal` ne regarde pas la nationalité d'une carte.

## Personnage (`mw_character`)

`Components/mw_character.py`, tests `tests/test_mw_character.py`. Une carte
**immobile** de `fixed` (`acquire` répond `success: false`), avec une colonne de
cases de chaque côté : `left` et `right`, chacune `{icon, boxes}`.
- `boxes` est ramené entre 3 et 6 ; une colonne absente, qui n'est pas un objet
  ou sans `boxes` numérique est omise (`read_column`). `icon` n'est qu'une
  image : jamais cliquée.
- `mark {component_id, column, index}` fait avancer une case
  (`MwCharacter.mark`) : à gauche (points de vie) 0 vide → 1 barre → 2 croix →
  0, à droite (fatigue de l'anneau) 0 vide → 1 rond → 0
  (`CHARACTER_COLUMN_STATES`). Diffusé à toute la session.
- Erreurs de `mark` : celles de `resolve_component_action`,
  `component_not_markable` (pas un `mw_character`, `Component.markable`), celles
  de `refuse_modification` (partie non lancée, joueur absent,
  `wrong_nationality`), `character_face_down` (dos visible : les colonnes n'y
  sont pas), `unknown_box` (colonne ou indice inconnu, indice non entier).
- Retourné par l'action `flip` existante (double clic) s'il a un `back_src` ;
  mêmes refus. `nationality` : seuls les joueurs de ce camp le cochent et le
  retournent (`acquirable_by`) ; sans, n'importe quel joueur. Ce n'est pas un
  `Token` : ses faces ne sont jamais masquées par `for_user`.
- `return_json` envoie et sauvegarde `side` et chaque colonne
  `{icon, boxes, marks}` ; `read_marks` relit des `marks` sauvegardées (valeur
  invalide → 0).
- **Conteneur de cartes** (`MwCharacter.cards`, la dernière posée en fin de
  liste) : une `Card` relâchée avec son centre sur le personnage
  (`Session.character_at`, après le sac et le paquet, et seulement si le
  joueur peut le toucher : nationalité) passe dessous
  (`put_under_character`) : elle quitte `movable`, perd sa position, passe
  face visible et droite. Tout le monde lit ses faces : `return_json` envoie
  `cards` (les `return_json` des cartes), sauvegardées et relues au
  chargement (`build_card` avec le personnage comme gabarit : taille et dos
  par défaut ; ids comptés par `duplicate_component_ids`).
- `pick` sur un `mw_character` (`pick_from_character`) rend la **dernière**
  carte posée, dans la main du joueur sous le pointeur
  (`take_from_character`) ; erreurs `wrong_nationality`, `character_empty`.
  Une carte sous un personnage est hors d'atteinte : `refuse_modification` →
  `component_under_character`, et un randomizer `id_list` l'ignore.

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
- Fonctions du serveur que les bots utilisent (mise à jour du 13/10/2026) :
  `deck` **et** `stack` sont des piles (`DECK_KINDS`) ; si `deal` est un
  `stack` et la main privée active, `Game.setup` distribue par l'action
  `deal` (six fois : humains compris, plus personne ne pioche sa donne),
  sinon `_deal_by_hand` garde l'ancienne distribution. `draw_many` /
  `draw_to_pile(count=…)` piochent par 10 (`count`, un événement `draw` par
  carte, le dernier a `index == count - 1`). Une carte sort d'une main sur la
  face qu'elle y a : `hand_to_table(face_down=True)` la retourne d'abord en
  main (`flip`), ce que fait `dump_hand`.
- Mains regardées : avec `private_hand.show_hands_to_other_players`,
  `Game.is_guilty` demande à l'humain de montrer sa main au bot
  (`read_shown_hand`, événement `show` → `TableClient.shows`,
  `shown_names`) et n'interroge la console qu'après `show_timeout` (120 s, option
  `--show-timeout`).
  Dans l'autre sens, les commandes `montre <bot> <pseudo|tous>` et
  `cache <bot>` ouvrent et ferment la zone du bot (`show_hand`, `hide_hand`).
- L'ordre du tour est celui de `participants`, donc l'ordre d'arrivée des
  joueurs, ou celui que l'owner a choisi dans la liste (`order_players`).
- Le serveur ne connaît ni tour ni règle : **le tour est déduit** des cartes
  posées sur la défausse et des pioches gardées en main. C'est pourquoi les
  événements `acquire`, `release`, `take`, `pick` et `draw` portent `nickname`.
- Les bots ne jouent jamais `alibi`, `thick_fog` ni `mycroft` (`NEVER_PLAYED`).
- Avec des humains, tout ce qui demande de se parler passe par la console du
  programme (`Operator`, commandes `aide`).
- Tests : `tests/test_sherlock_bot.py` fait jouer les bots contre la vraie
  `Session` et les vrais handlers, par une table en mémoire.

## `game_json` et `setup`

- `setup` est une liste de `{component_id, x?, y?, side?}` (et de
  `randomizer`, ci-dessous) décrivant
  l'installation initiale du plateau.
- Les coordonnées sont **absolues**, pas des deltas, et l'application est
  **idempotente** : réappliquer le même setup ne déplace rien deux fois.
- Les `component_id` inconnus sont ignorés sans erreur.
- `side` n'est appliqué qu'à un composant flippable.
- `game_json_state()` **retire** `setup` ; le champ n'est exposé qu'en attente,
  via `pending_setup`, puis consommé logiquement (la session ne le rejoue pas).
- Un pion déplacé par le setup adopte cette case comme **case d'origine**
  (`fix_position`).
- **`randomizer`** : une entrée `{kind: "randomizer", source_kind,
  source_id, targets}` du setup pose au hasard un composant sur chaque
  position de `targets` (`{x, y, side?, orientation?}`, `x`/`y` requis).
  `source_kind` : `bag`, `deck` ou `stack` (le conteneur `source_id`, qui doit
  être de ce kind ; pioche seulement pour un paquet), ou `id_list`
  (`source_id` est une liste d'id ; seuls les `Token`/`Card` connus et hors
  main comptent). `read_randomizer` le nettoie (mal écrit = ignoré).
  `Session.resolve_randomizers` tire **à la construction de la session**,
  après `load_session_components`, et remplace chaque randomizer par des
  entrées ordinaires `{component_id, x, y, side?, orientation?}` : tous les
  joueurs appliquent le même tirage et `_pending_sides` voit les faces. Un
  composant n'est tiré qu'une fois, tous randomizers confondus ; une source
  trop petite ne remplit que les premières positions. Les composants tirés
  sont notés dans `setup_take_out` : `apply_setup` les sort de leur conteneur
  (`take_out_for_setup`, vers `movable`), leur donne leur case de départ,
  applique `orientation` (`Token.tap`), et ajoute à l'événement `setup` le
  `return_json` de chaque sac ou paquet vidé. `game_json_state` garde le
  `setup` brut tant qu'il n'est pas appliqué : une session reconstruite avant
  tire à nouveau. `anonymize_ids` renomme aussi les id d'une `id_list`.
  Une cible peut aussi être un **conteneur** : `{container_id, pile?}`
  (`pile` : `draw` par défaut, ou `discard`), un deck ou un stack. Seule une
  `Card` y va (le tirage saute les autres) ; un conteneur inconnu, qui n'est
  pas un paquet ou sans cette pile, fait sauter la cible
  (`Session.container_target`). L'entrée résolue est
  `{component_id, container_id, pile}` ; `apply_setup` fait alors comme un
  relâcher sur la pile (`put_in_deck` : face de la pile, `orientation` du
  paquet), sauf si la carte y est déjà (setup rejoué), et envoie la carte sans
  position puis le paquet.
  Tests : `tests/test_randomizer.py`.
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

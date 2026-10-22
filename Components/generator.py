from Components.component import Component

# the kinds of components a generator hands out copies of: what moves on the table
GENERATED_KINDS = ('token', 'card')

# the face of a model a client does not need, by the side it shows
UNSHOWN_FACE = {'front': 'back_src', 'back': 'front_src'}


def read_models(components) -> list:
    """
    The models a generator lists: the descriptions of tokens and cards with an
    id, in the order of the game_json. Anything else is ignored, like an
    unknown kind in the "fixed" list.
    """
    if not isinstance(components, list):
        return []
    return [
        dict(component) for component in components
        if isinstance(component, dict)
        and component.get('kind') in GENERATED_KINDS
        and isinstance(component.get('id'), str) and component['id']
    ]


class Generator(Component):
    """
    A fixed component that hands out copies of the components it lists. It is
    not drawn on the table: the client shows its models in a column of their
    own, each on the face its description gives. Dragging a model out of the
    column creates a new copy, whose id is the id of the model followed by -1,
    -2, -3... A copy released back over the column is destroyed.

    The models are descriptions, not components: they are not indexed in
    components_dict and nothing acts on them. Only the copies are tokens or
    cards of the table.
    """
    def __init__(self, component_id, components=None, generated=None, created=None):
        super().__init__(component_id)
        self.kind = 'generator'
        self.models = read_models(components)
        # generated: the last number given to a copy of each model, so that a
        # copy never takes the id of an earlier one, even destroyed
        self.generated = {
            model_id: number for model_id, number in (generated or {}).items()
            if isinstance(model_id, str) and isinstance(number, int) and not isinstance(number, bool)
        } if isinstance(generated, dict) else {}
        # created: the ids of the copies still in the game; only those are
        # destroyed when released over the generator
        self.created = [
            copy_id for copy_id in created or [] if isinstance(copy_id, str)
        ] if isinstance(created, list) else []

    def model(self, model_id):
        """The description of the model model_id, or None."""
        for model in self.models:
            if model['id'] == model_id:
                return model
        return None

    def hides_faces(self) -> bool:
        """
        Whether a copy of one of the models may keep a face from some
        players: a card, or a token that belongs to a nationality.
        """
        return any(
            model['kind'] == 'card' or model.get('nationality')
            for model in self.models
        )

    def next_id(self, model_id, taken) -> str:
        """
        The id of the next copy of a model: its id followed by the next
        number. A number whose id is already taken by another component is
        skipped.
        :param taken: the ids already in use in the session
        """
        number = self.generated.get(model_id, 0)
        while True:
            number += 1
            copy_id = f"{model_id}-{number}"
            if copy_id not in taken:
                self.generated[model_id] = number
                return copy_id

    def copy_description(self, model_id, taken) -> dict | None:
        """
        The description of a new copy of a model, under its new id, or None
        when the generator has no such model. The copy is recorded as created.
        """
        model = self.model(model_id)
        if model is None:
            return None
        description = dict(model, id=self.next_id(model_id, taken), x=0, y=0)
        self.created.append(description['id'])
        return description

    def created_copy(self, component) -> bool:
        """Whether component is a copy this generator created and still exists."""
        return getattr(component, 'id', None) in self.created

    def destroy(self, component) -> None:
        """Forgets a copy destroyed over the generator."""
        self.created = [copy_id for copy_id in self.created if copy_id != component.id]

    def return_json(self, sat_list = None) -> dict:
        # a client only shows each model on its face: the other one stays on
        # the server, which sends each copy with what its players may read
        models = []
        for model in self.models:
            shown = dict(model)
            side = 'back' if model.get('side') == 'back' and model.get('back_src') else 'front'
            shown[UNSHOWN_FACE[side]] = None
            models.append(shown)
        return {
            "kind": self.kind,
            "id": self.id,
            "components": models,
        }

    def state_json(self) -> dict:
        return {
            "kind": self.kind,
            "id": self.id,
            "components": [dict(model) for model in self.models],
            "generated": dict(self.generated),
            "created": list(self.created),
        }

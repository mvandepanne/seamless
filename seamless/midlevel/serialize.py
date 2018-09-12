from copy import deepcopy

transformer_states = (
    ("input", "stored_state_input", True),
    ("input", "cached_state_input", False),
    ("result", "stored_state_result", True),
    ("result", "cached_state_result", False),
)


def extract(nodes, connections):
    topology = []
    values = {}
    cached_values = {}
    stored_states = {}
    cached_states = {}
    for path0, node in nodes.items():
        path = ".".join(path0)
        nodetype = node["type"]
        result = deepcopy(node)
        if nodetype == "cell":
            value = result.pop("value", None)
            if value is not None:
                values[path] = value
            cached_value = result.pop("cached_value", None)
            if cached_value is not None:
                cached_values[path] = cached_value
            stored_state = result.pop("stored_state", None)
            if stored_state is not None:
                stored_states[path] = stored_state.serialize()
            cached_state = result.pop("cached_state", None)
            if cached_state is not None:
                cached_states[path] = cached_state.serialize()
        elif nodetype == "transformer":
            if "values" in result:
                v = {".".join(k):v for k,v in result["values"].items()}
                result["values"] = v
            for sub, key, is_stored in transformer_states:
                state = result.pop(key, None)
                if state is not None:
                    states = stored_states if is_stored else cached_states
                    states[path+"."+sub] = state.serialize()
        topology.append(result)
    topology += connections
    return topology, values, cached_values, stored_states, cached_states
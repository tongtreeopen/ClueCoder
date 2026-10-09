from copy import deepcopy


def merge_config(base: dict, overrides: dict) -> dict:
    """Merge configurations without sharing mutable values with the inputs."""
    result = deepcopy(base)
    result.update(deepcopy(overrides))
    return result

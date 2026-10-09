def walk_leaves(value, path: tuple = ()) -> list[tuple]:
    """List (path, scalar) pairs from nested dictionaries and lists."""
    leaves = []
    if isinstance(value, dict):
        for key, child in value.items():
            leaves.extend(walk_leaves(child, (key,)))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            leaves.extend(walk_leaves(child, path + (index,)))
    else:
        leaves.append((path, value))
    return leaves

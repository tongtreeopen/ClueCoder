def stable_unique(items, key=None):
    selected = {}
    for item in items:
        selected[item if key is None else key(item)] = item
    return list(selected.values())

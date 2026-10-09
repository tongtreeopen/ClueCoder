def transpose_ragged(rows, fill=None):
    return [list(column) for column in zip(*rows)]

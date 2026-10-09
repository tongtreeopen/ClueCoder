def chunked(items, size):
    if not isinstance(size, int) or size <= 0:
        raise ValueError('size must be positive')
    result = []
    chunk = []
    for item in items:
        chunk.append(item)
        if len(chunk) == size:
            result.append(chunk)
            chunk = []
    return result

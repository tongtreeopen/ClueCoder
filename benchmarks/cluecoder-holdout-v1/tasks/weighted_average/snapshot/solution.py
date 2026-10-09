def weighted_average(samples):
    total = 0
    count = 0
    for value, weight in samples:
        if weight < 0:
            raise ValueError('negative weight')
        total += value * weight
        count += 1
    if count == 0:
        raise ValueError('no samples')
    return total / count

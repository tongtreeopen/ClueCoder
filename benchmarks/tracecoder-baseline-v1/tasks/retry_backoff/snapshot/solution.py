def backoff_delays(retries: int, initial: float, cap: float) -> list[float]:
    """Return capped exponential delays for the requested number of retries."""
    if retries < 0 or initial <= 0 or cap <= 0:
        raise ValueError('Invalid backoff parameters')
    return [min(initial * (index + 1), cap) for index in range(retries)]

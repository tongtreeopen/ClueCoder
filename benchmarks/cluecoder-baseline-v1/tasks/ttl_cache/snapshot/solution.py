class TTLCache:
    """A deterministic cache: callers supply timestamps in seconds."""

    def __init__(self):
        self._entries = {}

    def put(self, key, value, ttl: float, now: float) -> None:
        if ttl < 0:
            raise ValueError('Negative TTL')
        self._entries[key] = (value, now + ttl)

    def get(self, key, now: float, default=None):
        if key not in self._entries:
            return default
        value, expires = self._entries[key]
        if expires < now:
            del self._entries[key]
            return default
        return value

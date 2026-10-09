import unittest

from gate_api import invoke


class CacheTests(unittest.TestCase):
    def test_exact_expiry_and_eviction(self):
        self.assertEqual(invoke("""from solution import TTLCache
cache = TTLCache()
cache.put('key', 'value', 2, 10)
result = [cache.get('key', 11.999), cache.get('key', 12, 'expired'),
          cache.get('key', 10, 'removed')]
cache.put('zero', 123, 0, 20)
result.extend([cache.get('zero', 20), cache.get('missing', 1, 42)])
"""), ['value', 'expired', 'removed', None, 42])

    def test_replacement_falsey_values_and_invalid_ttl(self):
        self.assertEqual(invoke("""from solution import TTLCache
cache = TTLCache()
cache.put('x', 'old', 1, 0)
cache.put('x', False, 5, 0.5)
first = cache.get('x', 1)
try:
    cache.put('x', 'bad', -1, 2)
except ValueError:
    invalid = 'ValueError'
else:
    invalid = 'no error'
after_invalid = cache.get('x', 3)
expired = cache.get('x', 5.5, 'done')
cache.put('none', None, 1, 8)
none_value = cache.get('none', 8, 'absent')
result = [type(first).__name__, first, invalid, type(after_invalid).__name__,
          after_invalid, expired, none_value]
"""), ['bool', False, 'ValueError', 'bool', False, 'done', None])

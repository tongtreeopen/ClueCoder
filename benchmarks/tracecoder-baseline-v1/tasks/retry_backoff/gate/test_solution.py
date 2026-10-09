import unittest

from gate_api import invoke


class BackoffTests(unittest.TestCase):
    def test_growth_cap_and_boundaries(self):
        actual = invoke("""from solution import backoff_delays
cases = [(6, 0.5, 5), (4, 3, 100), (3, 8, 2), (0, 1, 2), (1, 1, 2), (1025, 0.5, 5)]
result = [[type(value).__name__, value] for value in (backoff_delays(*args) for args in cases)]
""")
        self.assertEqual(actual, [
            ['list', [0.5, 1, 2, 4, 5, 5]], ['list', [3, 6, 12, 24]], ['list', [2, 2, 2]],
            ['list', []], ['list', [1]], ['list', [0.5, 1, 2, 4] + [5] * 1021],
        ])

    def test_invalid_parameters(self):
        self.assertEqual(invoke("""from solution import backoff_delays
result = []
for args in [(-1, 1, 2), (1, 0, 2), (1, 1, 0), (1, -1, 2), (1, 1, -2)]:
    try:
        backoff_delays(*args)
    except ValueError:
        result.append('ValueError')
    else:
        result.append('no error')
"""), ['ValueError'] * 5)

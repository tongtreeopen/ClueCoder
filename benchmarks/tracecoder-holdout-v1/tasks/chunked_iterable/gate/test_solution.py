import unittest
from gate_api import invoke

class ChunkTests(unittest.TestCase):
    def test_complete_partial_empty_and_generator(self):
        self.assertEqual(invoke('''from solution import chunked
result = [chunked(iter(range(7)), 3), chunked((x for x in 'abcd'), 2), chunked([], 4), chunked(iter([None, 0]), 5)]
'''), [[[0, 1, 2], [3, 4, 5], [6]], [['a', 'b'], ['c', 'd']], [], [[None, 0]]])

    def test_invalid_size(self):
        self.assertEqual(invoke('''from solution import chunked
result = []
for size in [0, -2, 1.5]:
    try:
        chunked([1], size)
    except ValueError:
        result.append('ValueError')
    else:
        result.append('no error')
'''), ['ValueError'] * 3)

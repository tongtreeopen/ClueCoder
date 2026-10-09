import unittest
from gate_api import invoke

class TransposeTests(unittest.TestCase):
    def test_ragged_generators_and_custom_fill(self):
        self.assertEqual(invoke('''from solution import transpose_ragged
rows = [[1, 0, 3], [4], [], [None, 8]]
result = [transpose_ragged(iter(rows)), transpose_ragged((iter(row) for row in rows), fill='-'), rows, transpose_ragged([]), transpose_ragged([[], []])]
'''), [[[1, 4, None, None], [0, None, None, 8], [3, None, None, None]], [[1, 4, '-', None], [0, '-', '-', 8], [3, '-', '-', '-']], [[1, 0, 3], [4], [], [None, 8]], [], []])

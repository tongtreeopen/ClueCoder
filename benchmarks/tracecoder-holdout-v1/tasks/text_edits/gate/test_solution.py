import unittest
from gate_api import invoke

class EditTests(unittest.TestCase):
    def test_original_coordinates_and_insertion(self):
        self.assertEqual(invoke('''from solution import apply_edits
result = [apply_edits('abcdefghij', [(1, 3, 'X'), (5, 7, 'LONG')]), apply_edits('abcd', [(0, 0, '!'), (4, 4, '?')]), apply_edits('abc', [])]
'''), ['aXdeLONGhij', '!abcd?', 'abc'])

    def test_invalid_spans(self):
        self.assertEqual(invoke('''from solution import apply_edits
result = []
for edits in [[(-1, 1, 'x')], [(0, 4, 'x')], [(2, 1, 'x')], [(0, 2, 'x'), (1, 3, 'y')]]:
    try:
        apply_edits('abc', edits)
    except ValueError:
        result.append('ValueError')
    else:
        result.append('no error')
'''), ['ValueError'] * 4)

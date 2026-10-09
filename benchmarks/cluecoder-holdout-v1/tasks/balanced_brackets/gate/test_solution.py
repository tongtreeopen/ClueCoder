import unittest
from gate_api import invoke

class BracketTests(unittest.TestCase):
    def test_nested_mismatched_and_ignored(self):
        self.assertEqual(invoke('''from solution import balanced_brackets
result = [balanced_brackets(value) for value in ['', 'a{b[c(d)e]f}', '([)]', '([])', '}{', 'abc', '[(])', '(x[2]{y})', '((', '()[]{}']]
'''), [True, True, False, True, False, True, False, True, False, True])

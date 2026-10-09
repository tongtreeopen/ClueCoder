import unittest
from gate_api import invoke

class UniqueTests(unittest.TestCase):
    def test_first_value_and_order(self):
        self.assertEqual(invoke('''from solution import stable_unique
values = ['b', 'a', 'b', 'c', 'a']
result = [stable_unique(iter(values)), stable_unique((x for x in [0, False, 2, 0, 3])), values]
'''), [['b', 'a', 'c'], [0, 2, 3], ['b', 'a', 'b', 'c', 'a']])

    def test_key_preserves_first_object(self):
        self.assertEqual(invoke('''from solution import stable_unique
rows = [{'id': 2, 'v': 'first'}, {'id': 1, 'v': 'one'}, {'id': 2, 'v': 'last'}]
result = stable_unique(iter(rows), key=lambda row: row['id'])
'''), [{'id': 2, 'v': 'first'}, {'id': 1, 'v': 'one'}])

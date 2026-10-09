import unittest

from gate_api import invoke


class MergeTests(unittest.TestCase):
    def test_recursive_merge_and_replacement(self):
        self.assertEqual(invoke("""import copy
from solution import merge_config
base = {'db': {'host': 'local', 'auth': {'user': 'a', 'password': 'old'}}, 'ports': [1, 2], 'on': True}
overrides = {'db': {'auth': {'password': 'new'}}, 'ports': [], 'on': False, 'extra': None}
before = copy.deepcopy((base, overrides))
value = merge_config(base, overrides)
unchanged = (base, overrides) == before
value_before_change = copy.deepcopy(value)
value['db']['auth']['user'] = 'changed'
value['ports'].append(9)
result = [type(value).__name__, value_before_change, unchanged, (base, overrides) == before]
"""), [
            'dict', {'db': {'host': 'local', 'auth': {'user': 'a', 'password': 'new'}},
                     'ports': [], 'on': False, 'extra': None}, True, True,
        ])

    def test_type_changes_and_copying(self):
        self.assertEqual(invoke("""import copy
from solution import merge_config
base = {'keep': [1], 'change': {'x': 1}, 'nested': {'a': 1}}
overrides = {'change': 0, 'new': {'items': [2]}, 'nested': {}}
value = merge_config(base, overrides)
before_change = copy.deepcopy(value)
value['keep'].append(3)
value['new']['items'].append(4)
result = [type(value).__name__, before_change, base['keep'], overrides['new']['items'],
          merge_config({'x': 1}, {'x': {'y': 2}})]
"""), ['dict', {'keep': [1], 'change': 0, 'nested': {'a': 1}, 'new': {'items': [2]}},
         [1], [2], {'x': {'y': 2}}])

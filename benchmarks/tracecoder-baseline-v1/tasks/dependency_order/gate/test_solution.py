import unittest

from gate_api import invoke


class DependencyTests(unittest.TestCase):
    def test_order_shared_implicit_and_disconnected_nodes(self):
        self.assertEqual(invoke("""import copy
from solution import dependency_order
graph = {'app': ['db', 'cache'], 'db': ['shared'], 'cache': ['shared', 'implicit'], 'other': []}
before = copy.deepcopy(graph)
values = [dependency_order(graph), dependency_order({'a': ['b', 'b'], 'b': []}),
          dependency_order({}), dependency_order({'a': [], 'b': []})]
result = [[type(value).__name__, value] for value in values] + [graph == before]
"""), [['list', ['shared', 'db', 'implicit', 'cache', 'app', 'other']],
         ['list', ['b', 'a']], ['list', []], ['list', ['a', 'b']], True])

    def test_cycles(self):
        self.assertEqual(invoke("""from solution import dependency_order
result = []
for graph in [{'a': ['a']}, {'a': ['b'], 'b': ['a']},
              {'ok': [], 'a': ['b'], 'b': ['c'], 'c': ['a']}]:
    try:
        dependency_order(graph)
    except ValueError:
        result.append('ValueError')
    else:
        result.append('no error')
"""), ['ValueError'] * 3)

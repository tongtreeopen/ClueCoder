import unittest

from gate_api import invoke


class WalkTests(unittest.TestCase):
    def test_full_paths_and_input_preservation(self):
        self.assertEqual(invoke("""import copy
from solution import walk_leaves
def show(value):
    return [type(value).__name__, [[type(pair).__name__, type(pair[0]).__name__,
                                    list(pair[0]), pair[1]] for pair in value]]
tree = {'a': [{'b': 0}, {'c': [None, False]}], 'd': {'e': 'text'}, 'empty': {}}
before = copy.deepcopy(tree)
result = [show(walk_leaves(tree)), tree == before,
          show(walk_leaves({'x': {'y': 7}}, ('root',)))]
"""), [
            ['list', [['tuple', 'tuple', ['a', 0, 'b'], 0],
                      ['tuple', 'tuple', ['a', 1, 'c', 0], None],
                      ['tuple', 'tuple', ['a', 1, 'c', 1], False],
                      ['tuple', 'tuple', ['d', 'e'], 'text']]],
            True,
            ['list', [['tuple', 'tuple', ['root', 'x', 'y'], 7]]],
        ])

    def test_scalars_lists_and_empty_containers(self):
        self.assertEqual(invoke("""from solution import walk_leaves
def show(value):
    return [type(value).__name__, [[type(pair).__name__, type(pair[0]).__name__,
                                    list(pair[0]), pair[1]] for pair in value]]
result = [show(walk_leaves([1, [2, 3]])), show(walk_leaves('leaf')),
          show(walk_leaves([{}, []]))]
"""), [
            ['list', [['tuple', 'tuple', [0], 1], ['tuple', 'tuple', [1, 0], 2],
                      ['tuple', 'tuple', [1, 1], 3]]],
            ['list', [['tuple', 'tuple', [], 'leaf']]],
            ['list', []],
        ])

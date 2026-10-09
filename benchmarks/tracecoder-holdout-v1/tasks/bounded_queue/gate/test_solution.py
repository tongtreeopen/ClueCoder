import unittest
from gate_api import invoke

class QueueTests(unittest.TestCase):
    def test_overflow_order_and_falsey_items(self):
        self.assertEqual(invoke('''from solution import BoundedQueue
queue = BoundedQueue(3)
for value in [1, 0, None, 4, 5]:
    queue.push(value)
result = [len(queue), queue.pop(), queue.pop(), queue.pop(), len(queue)]
'''), [3, None, 4, 5, 0])

    def test_capacity_one_and_errors(self):
        self.assertEqual(invoke('''from solution import BoundedQueue
queue = BoundedQueue(1)
queue.push('old')
queue.push('new')
result = [queue.pop()]
try:
    queue.pop()
except IndexError:
    result.append('IndexError')
for capacity in [0, -1, 2.5]:
    try:
        BoundedQueue(capacity)
    except ValueError:
        result.append('ValueError')
'''), ['new', 'IndexError', 'ValueError', 'ValueError', 'ValueError'])

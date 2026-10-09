import unittest
from gate_api import invoke

class AverageTests(unittest.TestCase):
    def test_weights_and_generator(self):
        self.assertEqual(invoke('''from solution import weighted_average
values = [(10, 1), (20, 3), (100, 0)]
result = [weighted_average(iter(values)), weighted_average((pair for pair in [(4, 2), (10, 2)])), values]
'''), [17.5, 7.0, [[10, 1], [20, 3], [100, 0]]])

    def test_negative_and_zero_total(self):
        self.assertEqual(invoke('''from solution import weighted_average
result = []
for samples in [[], [(1, 0), (2, 0)], [(3, -1)]]:
    try:
        weighted_average(iter(samples))
    except ValueError:
        result.append('ValueError')
    else:
        result.append('no error')
'''), ['ValueError'] * 3)

import unittest

from gate_api import invoke


class LogTests(unittest.TestCase):
    def test_severity_order_and_iterables(self):
        actual = invoke("""import copy
from solution import filter_logs
records = [{'level': level, 'message': str(index)} for index, level in enumerate(
    ['error', 'debug', 'critical', 'warning', 'info', 'error'])]
before = copy.deepcopy(records)
values = [filter_logs(iter(records), 'warning'), filter_logs(records, 'critical'),
          filter_logs(records, 'debug'), filter_logs(records, 'info'), filter_logs([], 'error')]
result = [type(value).__name__ for value in values], values, records == before
""")
        records = [{'level': level, 'message': str(index)} for index, level in enumerate(
            ['error', 'debug', 'critical', 'warning', 'info', 'error'])]
        self.assertEqual(actual, [
            ['list'] * 5,
            [[records[i] for i in [0, 2, 3, 5]], [records[2]], records,
             [records[i] for i in [0, 2, 3, 4, 5]], []],
            True,
        ])

    def test_invalid_levels(self):
        self.assertEqual(invoke("""from solution import filter_logs
result = []
for records, level in [([], 'trace'), ([{'level': 'bogus'}], 'critical')]:
    try:
        filter_logs(records, level)
    except ValueError:
        result.append('ValueError')
    else:
        result.append('no error')
"""), ['ValueError', 'ValueError'])

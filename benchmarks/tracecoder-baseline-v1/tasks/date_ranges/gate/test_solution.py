import unittest

from gate_api import invoke


class DateTests(unittest.TestCase):
    def test_inclusive_end_and_calendar_boundaries(self):
        self.assertEqual(invoke("""from datetime import date
from solution import date_range
def show(value):
    return [type(value).__name__, [[type(day).__name__, day.isoformat()] for day in value]]
result = [show(date_range(date(2024, 2, 28), date(2024, 3, 1))),
          show(date_range(date(2023, 12, 30), date(2024, 1, 3), 2)),
          show(date_range(date(2025, 1, 1), date(2025, 1, 4), 2))]
"""), [
            ['list', [['date', '2024-02-28'], ['date', '2024-02-29'], ['date', '2024-03-01']]],
            ['list', [['date', '2023-12-30'], ['date', '2024-01-01'], ['date', '2024-01-03']]],
            ['list', [['date', '2025-01-01'], ['date', '2025-01-03']]],
        ])

    def test_equal_reversed_and_invalid_step(self):
        self.assertEqual(invoke("""from datetime import date
from solution import date_range
day = date(2025, 6, 1)
same = date_range(day, day)
reversed_range = date_range(date(2025, 6, 2), day)
result = [[type(same).__name__, [value.isoformat() for value in same]],
          [type(reversed_range).__name__, reversed_range]]
for step in [0, -1]:
    try:
        date_range(day, day, step)
    except ValueError:
        result.append('ValueError')
    else:
        result.append('no error')
"""), [['list', ['2025-06-01']], ['list', []], 'ValueError', 'ValueError'])

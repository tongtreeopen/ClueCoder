import unittest

from gate_api import invoke


class CsvTests(unittest.TestCase):
    def test_quotes_and_empty_fields(self):
        actual = invoke("""from solution import parse_record
records = ['alpha,"beta,gamma",delta', '"say \"\"hi\"\"\",x', ',"",last,',
           '  raw ," spaced "', '"a,b","c,d"', 'plain', '']
result = [[type(value).__name__, value] for value in map(parse_record, records)]
""")
        self.assertEqual(actual, [
            ['list', ['alpha', 'beta,gamma', 'delta']],
            ['list', ['say "hi"', 'x']],
            ['list', ['', '', 'last', '']],
            ['list', ['  raw ', ' spaced ']],
            ['list', ['a,b', 'c,d']],
            ['list', ['plain']],
            ['list', []],
        ])

import unittest
from gate_api import invoke

class QueryTests(unittest.TestCase):
    def test_repeated_decoded_and_empty_values(self):
        self.assertEqual(invoke('''from solution import parse_query
result = [parse_query('tag=one&tag=two&name=hello+world&x=%E4%B8%AD&blank=&flag&&a%26b=1%3D2'), parse_query(''), parse_query('&&x=0&&')]
'''), [{'tag': ['one', 'two'], 'name': ['hello world'], 'x': ['中'], 'blank': [''], 'flag': [''], 'a&b': ['1=2']}, {}, {'x': ['0']}])

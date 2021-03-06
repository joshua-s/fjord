from datetime import datetime
from unittest import TestCase

from nose.tools import eq_

from fjord.analytics.tools import (
    counts_to_options,
    generate_query_parsed,
    to_tokens,
    unescape,
    zero_fill)
from fjord.base.util import epoch_milliseconds


class TestQueryParsed(TestCase):
    def test_to_tokens_good(self):
        eq_(to_tokens(u'abc'),
            [u'abc'])

        eq_(to_tokens(u'abc def'),
            [u'abc', u'def'])

        eq_(to_tokens(u'abc OR "def"'),
            [u'abc', u'OR', u'"def"'])

        eq_(to_tokens(u'abc OR "def ghi"'),
            [u'abc', u'OR', u'"def ghi"'])

        eq_(to_tokens(u'abc AND "def ghi"'),
            [u'abc', u'AND', u'"def ghi"'])

    def test_escaping(self):
        """Escaped things stay escaped"""
        tests = [
            (u'\\"AND', [u'\\"AND']),
            (u'\\"AND\\"', [u'\\"AND\\"']),
        ]
        for text, expected in tests:
            eq_(to_tokens(text), expected)

    def test_to_tokens_edge_cases(self):
        eq_(to_tokens(u'AND "def ghi'),
            [u'AND', u'"def ghi"'])

    def test_unescape(self):
        tests = [
            (u'foo', u'foo'),
            (u'\\foo', u'foo'),
            (u'\\\\foo', u'\\foo'),
            (u'foo\\', u'foo'),
            (u'foo\\\\', u'foo\\'),
            (u'foo\\bar', u'foobar'),
            (u'foo\\\\bar', u'foo\\bar'),
        ]
        for text, expected in tests:
            eq_(unescape(text), expected)

    def test_query_parsed(self):
        self.assertEqual(
            generate_query_parsed('foo', u'abc'),
            {'text': {'foo': u'abc'}})

        self.assertEqual(
            generate_query_parsed('foo', u'abc def'),
            {'text': {'foo': u'abc def'}},
        )

        self.assertEqual(
            generate_query_parsed('foo', u'abc "def" ghi'),
            {
                'bool': {
                    'minimum_should_match': 1,
                    'should': [
                        {'text': {'foo': u'abc'}},
                        {'text_phrase': {'foo': u'def'}},
                        {'text': {'foo': u'ghi'}},
                    ]
                }
            }
        )

        self.assertEqual(
            generate_query_parsed('foo', u'abc AND "def"'),
            {
                'bool': {
                    'must': [
                        {'text': {'foo': u'abc'}},
                        {'text_phrase': {'foo': u'def'}},
                    ]
                }
            }
        )

        self.assertEqual(
            generate_query_parsed('foo', u'abc OR "def" AND ghi'),
            {
                'bool': {
                    'minimum_should_match': 1,
                    'should': [
                        {'text': {'foo': u'abc'}},
                        {'bool': {
                                'must': [
                                    {'text_phrase': {'foo': u'def'}},
                                    {'text': {'foo': u'ghi'}},
                                ]
                        }}
                    ]
                }
            }
        )

        self.assertEqual(
            generate_query_parsed('foo', u'abc AND "def" OR ghi'),
            {
                'bool': {
                    'must': [
                        {'text': {'foo': u'abc'}},
                        {'bool': {
                                'minimum_should_match': 1,
                                'should': [
                                    {'text_phrase': {'foo': u'def'}},
                                    {'text': {'foo': u'ghi'}},
                                ]
                        }}
                    ]
                }
            }
        )

        self.assertEqual(
            generate_query_parsed('foo', u'14.1\\" screen'),
            {'text': {'foo': u'14.1" screen'}}
        )

    def test_query_parsed_edge_cases(self):
        self.assertEqual(
            generate_query_parsed('foo', u'AND "def'),
            {
                'bool': {
                    'must': [
                        {'text_phrase': {'foo': u'def'}}
                    ]
                }
            }
        )

        self.assertEqual(
            generate_query_parsed('foo', u'"def" AND'),
            {
                'bool': {
                    'must': [
                        {'text_phrase': {'foo': u'def'}}
                    ]
                }
            }
        )

        self.assertEqual(
            generate_query_parsed('foo', u'foo\\bar'),
            {'text': {'foo': u'foobar'}}
        )

        self.assertEqual(
            generate_query_parsed('foo', u'foo\\\\bar'),
            {'text': {'foo': u'foo\\bar'}}
        )


class TestCountsHelper(TestCase):
    def setUp(self):
        self.counts = [('apples', 5), ('bananas', 10), ('oranges', 6)]

    def test_basic(self):
        """Correct options should be set and values should be sorted.
        """
        options = counts_to_options(self.counts, 'fruit', 'Fruit')
        eq_(options['name'], 'fruit')
        eq_(options['display'], 'Fruit')

        eq_(options['options'][0], {
            'name': 'bananas',
            'display': 'bananas',
            'value': 'bananas',
            'count': 10,
            'checked': False,
        })
        eq_(options['options'][1], {
            'name': 'oranges',
            'display': 'oranges',
            'value': 'oranges',
            'count': 6,
            'checked': False,
        })
        eq_(options['options'][2], {
            'name': 'apples',
            'display': 'apples',
            'value': 'apples',
            'count': 5,
            'checked': False,
        })

    def test_map_dict(self):
        options = counts_to_options(self.counts, 'fruit', display_map={
            'apples': 'Apples',
            'bananas': 'Bananas',
            'oranges': 'Oranges',
        })
        # Note that options get sorted by count.
        eq_(options['options'][0]['display'], 'Bananas')
        eq_(options['options'][1]['display'], 'Oranges')
        eq_(options['options'][2]['display'], 'Apples')

    def test_map_func(self):
        options = counts_to_options(self.counts, 'fruit',
            value_map=lambda s: s.upper())
        # Note that options get sorted by count.
        eq_(options['options'][0]['value'], 'BANANAS')
        eq_(options['options'][1]['value'], 'ORANGES')
        eq_(options['options'][2]['value'], 'APPLES')

    def test_checked(self):
        options = counts_to_options(self.counts, 'fruit', checked='apples')
        # Note that options get sorted by count.
        assert not options['options'][0]['checked']
        assert not options['options'][1]['checked']
        assert options['options'][2]['checked']


class TestZeroFillHelper(TestCase):
    def test_zerofill(self):
        start = datetime(2012, 1, 1)
        end = datetime(2012, 1, 7)
        data1 = {
            epoch_milliseconds(datetime(2012, 1, 3)): 1,
            epoch_milliseconds(datetime(2012, 1, 5)): 1,
        }
        data2 = {
            epoch_milliseconds(datetime(2012, 1, 2)): 1,
            epoch_milliseconds(datetime(2012, 1, 5)): 1,
            epoch_milliseconds(datetime(2012, 1, 10)): 1,
        }
        zero_fill(start, end, [data1, data2])

        for day in range(1, 8):
            millis = epoch_milliseconds(datetime(2012, 1, day))
            assert millis in data1, "Day %s was not zero filled." % day
            assert millis in data2, "Day %s was not zero filled." % day

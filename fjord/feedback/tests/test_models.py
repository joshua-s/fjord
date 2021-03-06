from nose.tools import eq_

from fjord.base.tests import TestCase
from fjord.feedback.models import Product, Response
from fjord.feedback.tests import response
from fjord.feedback.utils import compute_grams
from fjord.search.tests import ElasticTestCase


class TestResponseModel(TestCase):
    def test_description_truncate_on_save(self):
        # Extra 10 characters get lopped off on save.
        resp = response(description=('a' * 10010), save=True)
        eq_(resp.description, 'a' * 10000)

    def test_description_strip_on_save(self):
        # Nix leading and trailing whitespace.
        resp = response(description=u'   \n\tou812\t\n   ', save=True)
        eq_(resp.description, u'ou812')

    def test_url_domain(self):
        # Test a "normal domain"
        resp = response(url=u'http://foo.example.com.br/blah')
        eq_(resp.url_domain, u'example.com.br')
        assert isinstance(resp.url_domain, unicode)

        # Test a unicode domain
        resp = response(
            url=u'http://\u30c9\u30e9\u30af\u30a810.jp/dq10_skillpoint.html',
            save=True)
        eq_(resp.url_domain, u'\u30c9\u30e9\u30af\u30a810.jp')
        assert isinstance(resp.url_domain, unicode)


class TestAutoTranslation(TestCase):
    def setUp(self):
        # Wipe out translation system for all products.

        # FIXME - might be better to save the state and restore it in tearDown
        # rather than stomp in both cases. But stomping works for now.
        Product.objects.update(translation_system=u'')
        super(TestAutoTranslation, self).setUp()

    def tearDown(self):
        # Wipe out translation system for all products.
        Product.objects.update(translation_system=u'')
        super(TestAutoTranslation, self).tearDown()

    def test_auto_translation(self):
        prod = Product.uncached.get(db_name='firefox')
        prod.translation_system = u'dennis'
        prod.save()

        resp = response(
            locale=u'es',
            product=u'firefox',
            description=u'hola',
            save=True
        )

        # Fetch it from the db again
        resp = Response.uncached.get(id=resp.id)
        eq_(resp.translated_description, u'\xabHOLA\xbb')


class TestGenerateTranslationJobs(TestCase):
    def setUp(self):
        # Wipe out translation system for all products.

        # FIXME - might be better to save the state and restore it in tearDown
        # rather than stomp in both cases. But stomping works for now.
        Product.objects.update(translation_system=u'')
        super(TestGenerateTranslationJobs, self).setUp()

    def tearDown(self):
        # Wipe out translation system for all products.
        Product.objects.update(translation_system=u'')
        super(TestGenerateTranslationJobs, self).tearDown()

    def test_english_no_translation(self):
        """English descriptions should get copied over"""
        resp = response(
            locale=u'en-US',
            description=u'hello',
            translated_description=u'',
            save=True
        )

        # No new jobs should be generated
        eq_(len(resp.generate_translation_jobs()), 0)

        # Re-fetch from the db and make sure the description was copied over
        resp = Response.uncached.get(id=resp.id)
        eq_(resp.description, resp.translated_description)

    def test_english_with_dennis(self):
        """English descriptions should get copied over"""
        resp = response(
            locale=u'en-US',
            product=u'firefox',
            description=u'hello',
            translated_description=u'',
            save=True
        )

        # Set the product up for translation *after* creating the response
        # so that it doesn't get auto-translated because Response is set up
        # for auto-translation.
        prod = Product.uncached.get(db_name='firefox')
        prod.translation_system = u'dennis'
        prod.save()

        # No new jobs should be generated
        eq_(len(resp.generate_translation_jobs()), 0)

        # Re-fetch from the db and make sure the description was copied over
        resp = Response.uncached.get(id=resp.id)
        eq_(resp.description, resp.translated_description)

    def test_spanish_no_translation(self):
        """Spanish should not get translated"""
        resp = response(
            locale=u'es',
            product=u'firefox',
            description=u'hola',
            translated_description=u'',
            save=True
        )

        # No jobs should be translated
        eq_(len(resp.generate_translation_jobs()), 0)

        # Nothing should be translated
        eq_(resp.translated_description, u'')

    def test_spanish_with_dennis(self):
        """Spanish should get translated"""
        resp = response(
            locale=u'es',
            product=u'firefox',
            description=u'hola',
            translated_description=u'',
            save=True
        )

        # Set the product up for translation *after* creating the response
        # so that it doesn't get auto-translated because Response is set up
        # for auto-translation.
        prod = Product.uncached.get(db_name='firefox')
        prod.translation_system = u'dennis'
        prod.save()

        # One job should be generated
        jobs = resp.generate_translation_jobs()
        eq_(len(jobs), 1)
        job = jobs[0]
        eq_(job[1:], (u'dennis', u'es', u'description',
                      u'en-US', 'translated_description'))

        eq_(resp.translated_description, u'')

    def test_spanish_with_dennis_and_existing_translations(self):
        """Response should pick up existing translation"""
        existing_resp = response(
            locale=u'es',
            product=u'firefox',
            description=u'hola',
            translated_description=u'DUDE!',
            save=True
        )

        resp = response(
            locale=u'es',
            product=u'firefox',
            description=u'hola',
            translated_description=u'',
            save=True
        )

        # Set the product up for translation *after* creating the response
        # so that it doesn't get auto-translated because Response is set up
        # for auto-translation.
        prod = Product.uncached.get(db_name='firefox')
        prod.translation_system = u'dennis'
        prod.save()

        # No jobs should be translated
        eq_(len(resp.generate_translation_jobs()), 0)
        eq_(resp.translated_description, existing_resp.translated_description)


class TestComputeGrams(ElasticTestCase):
    def test_empty(self):
        eq_(compute_grams(u''), [])

    def test_parsing(self):
        # stop words are removed
        eq_(compute_grams(u'i me him her'), [])

        # capital letters don't matter
        eq_(compute_grams(u'I ME HIM HER'), [])

        # punctuation nixed
        eq_(compute_grams(u'i, me, him, her'), [])

    def test_bigrams(self):
        # Note: Tokens look weird after being analyzed probably due to
        # the stemmer. We could write a bunch of code to "undo" some
        # of the excessive stemming, but it's probably an exercise in
        # futility. Ergo the tests look a little odd. e.g. "youtub"

        # One word a bigram does not make
        eq_(compute_grams(u'youtube'), [])

        # Two words is the minimum number to create a bigram
        eq_(sorted(compute_grams(u'youtube crash')),
            ['crash youtub'])

        # Three words creates two bigrams
        eq_(sorted(compute_grams(u'youtube crash flash')),
            ['crash flash', 'crash youtub'])

        # Four words creates three bigrams
        eq_(sorted(compute_grams(u'youtube crash flash bridge')),
            ['bridg flash', 'crash flash', 'crash youtub'])

        # Nix duplicate bigrams
        eq_(sorted(compute_grams(u'youtube crash youtube flash')),
            ['crash youtub', 'flash youtub'])

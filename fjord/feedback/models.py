from datetime import datetime
import urlparse

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import models

from elasticutils.contrib.django import Indexable
from rest_framework import serializers
from tower import ugettext_lazy as _
from product_details import product_details

from fjord.base.domain import get_domain
from fjord.base.models import ModelBase
from fjord.base.util import smart_truncate
from fjord.feedback.config import CODE_TO_COUNTRY
from fjord.feedback.utils import compute_grams
from fjord.search.index import (
    register_mapping_type, FjordMappingType,
    boolean_type, date_type, integer_type, keyword_type, terms_type,
    text_type)
from fjord.search.tasks import register_live_index
from fjord.translations.models import get_translation_system_choices
from fjord.translations.tasks import register_auto_translation
from fjord.translations.utils import compose_key


# This defines the number of characters the description can have.  We
# do this in code rather than in the db since it makes it easier to
# tweak the value.
TRUNCATE_LENGTH = 10000


class Product(ModelBase):
    """Represents a product we capture feedback for"""
    # Whether or not this product is enabled
    enabled = models.BooleanField(default=True)

    # Used internally for notes to make it easier to manage products
    notes = models.CharField(max_length=255, blank=True, default=u'')

    # This is the name we display everywhere
    display_name = models.CharField(max_length=20)

    # We're not using foreign keys, so when we save something to the
    # database, we use this name
    db_name = models.CharField(max_length=20)

    # This is the slug used in the feedback product urls; we don't use
    # the SlugField because we don't require slugs be unique
    slug = models.CharField(max_length=20)

    # Whether or not this product shows up on the dashboard
    on_dashboard = models.BooleanField(default=True)

    # System slated for automatic translation, or null if none;
    # See translation app for details.
    translation_system = models.CharField(
        choices=get_translation_system_choices(),
        null=True,
        blank=True,
        max_length=20,
    )

    @classmethod
    def get_product_map(cls):
        """Returns map of product slug -> db_name"""
        products = cls.objects.values_list('slug', 'db_name')
        return dict(prod for prod in products)


@register_auto_translation
@register_live_index
class Response(ModelBase):
    """Basic feedback response

    This consists of a bunch of information some of which is inferred
    and some of which comes from the source.

    Some fields are "sacrosanct" and should never be edited after the
    response was created:

    * happy
    * url
    * description
    * user_agent
    * manufacturer
    * device
    * created

    """

    # This is the product/channel.
    # e.g. "firefox.desktop.stable", "firefox.mobile.aurora", etc.
    prodchan = models.CharField(max_length=255)

    # Data coming from the user
    happy = models.BooleanField(default=True)
    url = models.URLField(blank=True)
    description = models.TextField(blank=True)

    # Translation into English of the description
    translated_description = models.TextField(blank=True)

    # Data inferred from urls or explicitly stated by the thing saving
    # the data (webform, client of the api, etc)
    product = models.CharField(max_length=30, blank=True)
    channel = models.CharField(max_length=30, blank=True)
    version = models.CharField(max_length=30, blank=True)
    locale = models.CharField(max_length=8, blank=True)
    country = models.CharField(max_length=4, blank=True, null=True,
                               default=u'')

    manufacturer = models.CharField(max_length=255, blank=True)
    device = models.CharField(max_length=255, blank=True)

    # User agent and inferred data from the user agent
    user_agent = models.CharField(max_length=255, blank=True)
    browser = models.CharField(max_length=30, blank=True)
    browser_version = models.CharField(max_length=30, blank=True)
    platform = models.CharField(max_length=30, blank=True)

    source = models.CharField(max_length=100, blank=True, null=True,
                              default=u'')
    campaign = models.CharField(max_length=100, blank=True, null=True,
                                default=u'')

    created = models.DateTimeField(default=datetime.now)

    class Meta:
        ordering = ['-created']

    def __unicode__(self):
        return u'(%s) %s' % (self.sentiment, self.truncated_description)

    def __repr__(self):
        return self.__unicode__().encode('ascii', 'ignore')

    def generate_translation_jobs(self):
        """Returns a list of tuples, one for each translation job

        If the locale of this response is English, then we just copy over
        the description and we're done.

        If the product of this response isn't set up for auto-translation,
        then we're done.

        If we already have a response with this text that's
        translated, we copy the most recent translation over.

        Otherwise we generate a list of jobs to be done.

        .. Note::

           This happens in a celery task, so feel free to do what you need
           to do here.

        """
        # If the text is in English, we copy it over and we're
        # done. We do this regardless of whether auto-translation is
        # enabled or not for this product.
        if self.locale == 'en-US':
            self.translated_description = self.description
            self.save()
            return []

        try:
            prod = Product.objects.get(db_name=self.product)
            system = prod.translation_system
        except Product.DoesNotExist:
            # If the product doesn't exist, then I don't know what's
            # going on, but we shouldn't create any translation jobs
            return []

        if not system:
            # If this product isn't set up for translation, don't
            # translate it.
            return []

        try:
            # See if this text has been translated already--if so, use
            # the most recent translation.
            existing_obj = (
                Response.objects
                .filter(description=self.description)
                .exclude(translated_description__isnull=True)
                .exclude(translated_description=u'')
                .latest('id'))
            self.translated_description = existing_obj.translated_description
            self.save()
            return []
        except Response.DoesNotExist:
            pass

        return [
            # key, system, src language, src field, dst language, dst field
            (compose_key(self), system, self.locale, 'description',
             u'en-US', 'translated_description')
        ]

    @classmethod
    def get_export_keys(cls, confidential=False):
        """Returns set of keys that are interesting for export

        Some parts of the Response aren't very interesting. This lets
        us explicitly state what is available for export.

        Note: This returns the name of *properties* of Response which
        aren't all database fields. Some of them are finessed.

        :arg confidential: Whether or not to include confidential data

        """
        keys = [
            'id',
            'created',
            'sentiment',
            'description',
            'translated_description',
            'product',
            'channel',
            'version',
            'locale_name',
            'manufacturer',
            'device',
            'platform',
        ]

        if confidential:
            keys.extend([
                'url',
                'country_name',
                'user_email',
            ])
        return keys

    def save(self, *args, **kwargs):
        self.description = self.description.strip()[:TRUNCATE_LENGTH]
        super(Response, self).save(*args, **kwargs)

    @property
    def url_domain(self):
        """Returns the domain part of a url"""
        return get_domain(self.url)

    @property
    def user_email(self):
        """Associated email address or u''"""
        if self.responseemail_set.count() > 0:
            return self.responseemail_set.all()[0].email
        return u''

    @property
    def sentiment(self):
        if self.happy:
            return _(u'Happy')
        return _(u'Sad')

    @property
    def truncated_description(self):
        """Shorten feedback for list display etc."""
        return smart_truncate(self.description, length=70)

    @property
    def locale_name(self, native=False):
        """Convert a locale code into a human readable locale name"""
        locale = self.locale
        if locale in product_details.languages:
            display_locale = 'native' if native else 'English'
            return product_details.languages[locale][display_locale]

        return locale

    @property
    def country_name(self, native=False):
        """Convert a country code into a human readable country name"""
        country = self.country
        if country in CODE_TO_COUNTRY:
            display_locale = 'native' if native else 'English'
            return CODE_TO_COUNTRY[country][display_locale]

        return country

    @classmethod
    def get_mapping_type(self):
        return ResponseMappingType

    @classmethod
    def infer_product(cls, platform):
        if platform == u'Firefox OS':
            return u'Firefox OS'

        elif platform == u'Android':
            return u'Firefox for Android'

        elif platform in (u'', u'Unknown'):
            return u''

        return u'Firefox'


@register_mapping_type
class ResponseMappingType(FjordMappingType, Indexable):
    @classmethod
    def get_model(cls):
        return Response

    @classmethod
    def get_mapping(cls):
        return {
            'id': integer_type(),
            'prodchan': keyword_type(),
            'happy': boolean_type(),
            'url': keyword_type(),
            'url_domain': keyword_type(),
            'has_email': boolean_type(),
            'description': text_type(),
            'description_bigrams': keyword_type(),
            'description_terms': terms_type(),
            'user_agent': keyword_type(),
            'product': keyword_type(),
            'channel': keyword_type(),
            'version': keyword_type(),
            'browser': keyword_type(),
            'browser_version': keyword_type(),
            'platform': keyword_type(),
            'locale': keyword_type(),
            'country': keyword_type(),
            'device': keyword_type(),
            'manufacturer': keyword_type(),
            'created': date_type()
        }

    @classmethod
    def extract_document(cls, obj_id, obj=None):
        if obj is None:
            obj = cls.get_model().objects.get(pk=obj_id)

        def empty_to_unknown(text):
            return u'Unknown' if text == u'' else text

        doc = {
            'id': obj.id,
            'prodchan': obj.prodchan,
            'happy': obj.happy,
            'url': obj.url,
            'url_domain': obj.url_domain,
            'has_email': bool(obj.user_email),
            'description': obj.description,
            'description_terms': obj.description,
            'user_agent': obj.user_agent,
            'product': obj.product,
            'channel': obj.channel,
            'version': obj.version,
            'browser': obj.browser,
            'browser_version': obj.browser_version,
            'platform': obj.platform,
            'locale': obj.locale,
            'country': obj.country,
            'device': obj.device,
            'manufacturer': obj.manufacturer,
            'created': obj.created,
        }

        # We only compute bigrams for english because the analysis
        # uses English stopwords, stemmers, ...
        if obj.locale.startswith(u'en') and obj.description:
            bigrams = compute_grams(obj.description)
            doc['description_bigrams'] = bigrams

        return doc

    @property
    def truncated_description(self):
        """Shorten feedback for dashboard view."""
        return smart_truncate(self.description, length=500)

    @classmethod
    def get_products(cls):
        """Returns a list of all products

        This is cached.

        """
        key = 'feedback:response_products1'
        products = cache.get(key)
        if products is not None:
            return products

        facet = cls.search().facet('product').facet_counts()
        products = [prod['term'] for prod in facet['product']]

        cache.add(key, products)
        return products

    @classmethod
    def get_indexable(cls):
        return super(ResponseMappingType, cls).get_indexable().reverse()


class ResponseEmail(ModelBase):
    """Holds email addresses related to Responses."""

    opinion = models.ForeignKey(Response)
    email = models.EmailField()


class NoNullsCharField(serializers.CharField):
    """Further restricts CharField so it doesn't accept nulls

    DRF lets CharFields take nulls which is not what I want. This
    raises a ValidationError if the value is a null.

    """
    def from_native(self, value):
        if value is None:
            raise ValidationError('Value cannot be null')
        return super(NoNullsCharField, self).from_native(value)


class ResponseSerializer(serializers.Serializer):
    """This handles incoming feedback

    This handles responses as well as the additional data for response
    emails.

    """
    happy = serializers.BooleanField(required=True)
    url = serializers.URLField(required=False, default=u'')
    description = serializers.CharField(required=True)

    # Note: API clients don't provide a user_agent, so we skip that and
    # browser since those don't make sense.

    # product, channel, version, locale, platform
    product = NoNullsCharField(max_length=20, required=True)
    channel = NoNullsCharField(max_length=30, required=False, default=u'')
    version = NoNullsCharField(max_length=30, required=False, default=u'')
    locale = NoNullsCharField(max_length=8, required=False, default=u'')
    platform = NoNullsCharField(max_length=30, required=False, default=u'')
    country = NoNullsCharField(max_length=4, required=False, default=u'')

    # device information
    manufacturer = NoNullsCharField(required=False, default=u'')
    device = NoNullsCharField(required=False, default=u'')

    # user's email address
    email = serializers.EmailField(required=False)

    def validate_product(self, attrs, source):
        """Validates the product against Product model"""
        value = attrs[source]

        # This looks goofy, but it makes it more likely we have a
        # cache hit.
        products = Product.objects.values_list('display_name', flat=True)
        if value not in products:
            raise serializers.ValidationError(
                '{0} is not a valid product'.format(value))
        return attrs

    def restore_object(self, attrs, instance=None):
        # Note: instance should never be anything except None here
        # since we only accept POST and not PUT/PATCH.

        # prodchan is composed of product + channel. This is a little
        # goofy, but we can fix it later if we bump into issues with
        # the contents.
        prodchan = u'.'.join([
            attrs['product'].lower().replace(' ', '') or 'unknown',
            attrs['channel'].lower().replace(' ', '') or 'unknown'])

        opinion = Response(
            prodchan=prodchan,
            happy=attrs['happy'],
            url=attrs['url'].strip(),
            description=attrs['description'].strip(),
            user_agent=u'api',  # Hard-coded
            product=attrs['product'].strip(),
            channel=attrs['channel'].strip(),
            version=attrs['version'].strip(),
            platform=attrs['platform'].strip(),
            locale=attrs['locale'].strip(),
            manufacturer=attrs['manufacturer'].strip(),
            device=attrs['device'].strip(),
            country=attrs['country'].strip()
        )

        # If there is an email address, stash it on this instance so
        # we can save it later in .save() and so it gets returned
        # correctly in the response. This doesn't otherwise affect the
        # Response model instance.
        opinion.email = attrs.get('email', '').strip()

        return opinion

    def save_object(self, obj, **kwargs):
        obj.save(**kwargs)

        if obj.email:
            opinion_email = ResponseEmail(
                email=obj.email,
                opinion=obj
            )
            opinion_email.save(**kwargs)

        return obj

import os
import six
from datetime import date, datetime, time

import django
from django.db import models
from django.db.models.query import QuerySet
from django.db.models import sql
from django.contrib.contenttypes.models import ContentType
from django.contrib.auth.models import User
# Deprecated this thing in Django 1.8 and removed in 1.10
if django.VERSION < (1, 8):
    from django.db.models import SubfieldBase
else:
    class SubfieldBase(type):
        pass


### For basic tests and bench

class Category(models.Model):
    title = models.CharField(max_length=128)

    def __unicode__(self):
        return self.title

class Post(models.Model):
    title = models.CharField(max_length=128)
    category = models.ForeignKey(Category, related_name='posts')
    visible = models.BooleanField(default=True)

    def __unicode__(self):
        return self.title

class Extra(models.Model):
    post = models.OneToOneField(Post)
    tag = models.IntegerField(db_column='custom_column_name', unique=True)
    to_tag = models.ForeignKey('self', to_field='tag', null=True)

    def __unicode__(self):
        return 'Extra(post_id=%s, tag=%s)' % (self.post_id, self.tag)


### Specific and custom fields

class CustomValue(object):
    def __init__(self, value):
        self.value = value

    def __str__(self):
        return str(self.value)

    def __eq__(self, other):
        return isinstance(other, CustomValue) and self.value == other.value

class CustomField(six.with_metaclass(SubfieldBase, models.Field)):
    def db_type(self, connection):
        return 'text'

    def to_python(self, value):
        if isinstance(value, CustomValue):
            return value
        return CustomValue(value)

    def from_db_value(self, value, expession, conn, context):
        return self.to_python(value)

    def get_prep_value(self, value):
        return value.value

class CustomWhere(sql.where.WhereNode):
    pass

class CustomQuery(sql.Query):
    pass

class CustomManager(models.Manager):
    def get_query_set(self):
        q = CustomQuery(self.model, CustomWhere)
        return QuerySet(self.model, q)
    get_queryset = get_query_set


class IntegerArrayField(six.with_metaclass(SubfieldBase, models.Field)):
    def db_type(self, connection):
        return 'text'

    def to_python(self, value):
        if value in (None, ''):
            return None
        if isinstance(value, list):
            return value
        return [int(v) for v in value.split(',')]

    def from_db_value(self, value, expession, conn, context):
        return self.to_python(value)

    def get_prep_value(self, value):
        return ','.join(map(str, value))

def custom_value_default():
    return CustomValue('default')

class Weird(models.Model):
    date_field = models.DateField(default=date(2000, 1, 1))
    datetime_field = models.DateTimeField(default=datetime(2000, 1, 1, 10, 10))
    time_field = models.TimeField(default=time(10, 10))
    list_field = IntegerArrayField(default=list, blank=True)
    custom_field = CustomField(default=custom_value_default)
    if hasattr(models, 'BinaryField'):
        binary_field = models.BinaryField()

    objects = models.Manager()
    customs = CustomManager()

# TODO: check other new fields:
#       - PostgreSQL ones: ArrayField, HStoreField, RangeFields, unaccent
#       - Other: UUIDField, DurationField
# contrib.postgres ArrayField
try:
    from django.contrib.postgres.fields import ArrayField
except ImportError:
    ArrayField = None

if ArrayField and os.environ.get('CACHEOPS_DB') != 'mysql':
    class TaggedPost(models.Model):
        name = models.CharField(max_length=200)
        tags = ArrayField(models.IntegerField())


# 16
class Profile(models.Model):
    user = models.ForeignKey(User)
    tag = models.IntegerField()


# Proxy model
class Video(models.Model):
    title = models.CharField(max_length=128)

class VideoProxy(Video):
    class Meta:
        proxy = True

class NonCachedVideoProxy(Video):
    class Meta:
        proxy = True

class NonCachedMedia(models.Model):
    title = models.CharField(max_length=128)

class MediaProxy(NonCachedMedia):
    class Meta:
        proxy = True


# Multi-table inheritance
class Media(models.Model):
    name = models.CharField(max_length=128)

class Movie(Media):
    year = models.IntegerField()


# Decimals
class Point(models.Model):
    x = models.DecimalField(decimal_places=6, max_digits=8, blank=True, default=0.0)


# M2M models
class Label(models.Model):
    text = models.CharField(max_length=127, blank=True, default='')

class Brand(models.Model):
    labels = models.ManyToManyField(Label, related_name='brands')

# M2M with explicit through models
class LabelT(models.Model):
    text = models.CharField(max_length=127, blank=True, default='')

class BrandT(models.Model):
    labels = models.ManyToManyField(LabelT, related_name='brands', through='Labeling')

class Labeling(models.Model):
    label = models.ForeignKey(LabelT)
    brand = models.ForeignKey(BrandT)
    tag = models.IntegerField()

class PremiumBrand(Brand):
    extra = models.CharField(max_length=127, blank=True, default='')

# local_get
class Local(models.Model):
    tag = models.IntegerField(null=True)


# 45
class CacheOnSaveModel(models.Model):
    title = models.CharField(max_length=32)


# 47
class DbAgnostic(models.Model):
    pass

class DbBinded(models.Model):
    pass


# 62
class Product(models.Model):
    name = models.CharField(max_length=32)

class ProductReview(models.Model):
    product = models.ForeignKey(Product, related_name='reviews', null=True)
    status = models.IntegerField()


# 70
try:
    from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
except ImportError:
    from django.contrib.contenttypes.generic import GenericForeignKey, GenericRelation

class GenericContainer(models.Model):
    content_type = models.ForeignKey(ContentType)
    object_id = models.PositiveIntegerField()
    content_object = GenericForeignKey('content_type', 'object_id')
    name = models.CharField(max_length=30)

class Contained(models.Model):
    name = models.CharField(max_length=30)
    containers = GenericRelation(GenericContainer)


# 117
class All(models.Model):
    tag = models.IntegerField(null=True)


# contrib.postgis
if os.environ.get('CACHEOPS_DB') == 'postgis':
    from django.contrib.gis.db import models as gis_models

    class Geometry(gis_models.Model):
        point = gis_models.PointField(geography=True, dim=3, blank=True, null=True, default=None)

# 145
class One(models.Model):
    boolean = models.BooleanField(default=False)

def set_boolean_true(sender, instance, created, **kwargs):
    if created:
        return

    dialog = One.objects.cache(ops='all').get(id=instance.id)
    assert dialog.boolean is True

# 159
class M2MBase(models.Model):
    char_many_to_many = models.ManyToManyField('M2MWithCharId')

class M2MWithCharId(models.Model):
    id = models.CharField(max_length=30, primary_key=True)
    name = models.CharField(max_length=30)


from django.db.models.signals import post_save
post_save.connect(set_boolean_true, sender=One)

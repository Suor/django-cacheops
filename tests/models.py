import os
import uuid
from datetime import date, time

from django.db import models
from django.db.models.query import QuerySet
from django.db.models import sql, manager
from django.contrib.auth.models import User
from django.utils import timezone


### For basic tests and bench

class Category(models.Model):
    title = models.CharField(max_length=128)

    def __unicode__(self):
        return self.title

class Post(models.Model):
    title = models.CharField(max_length=128)
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name='posts')
    visible = models.BooleanField(default=True)

    def __unicode__(self):
        return self.title

class Extra(models.Model):
    post = models.OneToOneField(Post, on_delete=models.CASCADE)
    tag = models.IntegerField(db_column='custom_column_name', unique=True)
    to_tag = models.ForeignKey('self', on_delete=models.CASCADE, to_field='tag', null=True)

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

class CustomField(models.Field):
    def db_type(self, connection):
        return 'text'

    def to_python(self, value):
        if isinstance(value, CustomValue):
            return value
        return CustomValue(value)

    def from_db_value(self, value, expession, conn):
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


class IntegerArrayField(models.Field):
    def db_type(self, connection):
        return 'text'

    def to_python(self, value):
        if value in (None, ''):
            return None
        if isinstance(value, list):
            return value
        return [int(v) for v in value.split(',')]

    def from_db_value(self, value, expession, conn):
        return self.to_python(value)

    def get_prep_value(self, value):
        return ','.join(map(str, value))

def custom_value_default():
    return CustomValue('default')

class Weird(models.Model):
    date_field = models.DateField(default=date(2000, 1, 1))
    datetime_field = models.DateTimeField(default=timezone.now)
    time_field = models.TimeField(default=time(10, 10))
    list_field = IntegerArrayField(default=list, blank=True)
    custom_field = CustomField(default=custom_value_default)
    binary_field = models.BinaryField()

    objects = models.Manager()
    customs = CustomManager()


# TODO: check other new fields:
#       - PostgreSQL ones: HStoreField, RangeFields, unaccent
#       - Other: DurationField
if os.environ.get('CACHEOPS_DB') in {'postgresql', 'postgis'}:
    from django.contrib.postgres.fields import ArrayField
    try:
        from django.db.models import JSONField
    except ImportError:
        try:
            from django.contrib.postgres.fields import JSONField  # Used before Django 3.1
        except ImportError:
            JSONField = None

    class TaggedPost(models.Model):
        name = models.CharField(max_length=200)
        tags = ArrayField(models.IntegerField())
        if JSONField:
            meta = JSONField()


# 16
class Profile(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
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


class MediaType(models.Model):
    name = models.CharField(max_length=50)

# Multi-table inheritance
class Media(models.Model):
    name = models.CharField(max_length=128)
    media_type = models.ForeignKey(
        MediaType,
        on_delete=models.CASCADE,
    )

    def __str__(self):
        return str(self.media_type)


class Movie(Media):
    year = models.IntegerField()


class Scene(models.Model):
    """Model with FK to submodel."""
    name = models.CharField(max_length=50)
    movie = models.ForeignKey(
        Movie,
        on_delete=models.CASCADE,
        related_name="scenes",
    )

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
    label = models.ForeignKey(LabelT, on_delete=models.CASCADE)
    brand = models.ForeignKey(BrandT, on_delete=models.CASCADE)
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

    dialog = One.objects.cache().get(id=instance.id)
    assert dialog.boolean is True

from django.db.models.signals import post_save
post_save.connect(set_boolean_true, sender=One)


# 312
class Device(models.Model):
    uid = models.UUIDField(default=uuid.uuid4)
    model = models.CharField(max_length=64)


# 333
class CustomQuerySet(QuerySet):
    pass


class CustomFromQSManager(manager.BaseManager.from_queryset(CustomQuerySet)):
    use_for_related_fields = True


class CustomFromQSModel(models.Model):
    boolean = models.BooleanField(default=False)
    objects = CustomFromQSManager()


# 352
class CombinedField(models.CharField):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.another_field = models.CharField(*args, **kwargs)

    def contribute_to_class(self, cls, name, **kwargs):
        super().contribute_to_class(cls, name, private_only=True)
        self.another_field.contribute_to_class(cls, name, **kwargs)


class CombinedFieldModel(models.Model):
    text = CombinedField(max_length=8, default='example')


# 353
class Foo(models.Model):
    pass


class Bar(models.Model):
    foo = models.OneToOneField(
        to="Foo",
        on_delete=models.SET_NULL,
        related_name='bar',
        blank=True,
        null=True
    )


# 385
class Client(models.Model):
    def __init__(self, *args, **kwargs):
        # copied from Django 2.1.5 (not exists in Django 3.1.5 installed by current requirements)
        def curry(_curried_func, *args, **kwargs):
            def _curried(*moreargs, **morekwargs):
                return _curried_func(*args, *moreargs, **{**kwargs, **morekwargs})

            return _curried

        super().__init__(*args, **kwargs)
        setattr(self, '_get_private_data', curry(sum, [1, 2, 3, 4]))

    name = models.CharField(max_length=255)


# Abstract models
class Abs(models.Model):
    class Meta:
        abstract = True

class Concrete1(Abs):
    pass

class AbsChild(Abs):
    class Meta:
        abstract = True

class Concrete2(AbsChild):
    pass

class NoProfile(models.Model):
    title = models.CharField(max_length=128)

class NoProfileProxy(NoProfile):
    class Meta:
        proxy = True

class AbsNoProfile(NoProfile):
    class Meta:
        abstract = True

class NoProfileChild(AbsNoProfile):
    pass


class ParentId(models.Model):
    pass

class ParentStr(models.Model):
    name = models.CharField(max_length=128, primary_key=True)

class Mess(ParentId, ParentStr):
    pass

class MessChild(Mess):
    pass

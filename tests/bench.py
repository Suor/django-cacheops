from cacheops import invalidate_obj, invalidate_model
from cacheops.redis import redis_client
from cacheops.cross import pickle

from .models import Category, Post, Extra


posts = list(Post.objects.cache().all())
posts_pickle = pickle.dumps(posts, -1)

def do_pickle():
    pickle.dumps(posts, -1)

def do_unpickle():
    pickle.loads(posts_pickle)


get_key = Category.objects.filter(pk=1).order_by()._cache_key()
def invalidate_get():
    redis_client.delete(get_key)

def do_get():
    Category.objects.cache().get(pk=1)

def do_get_no_cache():
    Category.objects.nocache().get(pk=1)


c = Category.objects.first()
def invalidate_count():
    invalidate_obj(c)

def do_count():
    Category.objects.cache().count()

def do_count_no_cache():
    Category.objects.nocache().count()


fetch_qs = Category.objects.all()
fetch_key = fetch_qs._cache_key()

def invalidate_fetch():
    redis_client.delete(fetch_key)

def do_fetch():
    list(Category.objects.cache().all())

def do_fetch_no_cache():
    list(Category.objects.nocache().all())

def do_fetch_construct():
    Category.objects.all()

def do_fetch_cache_key():
    fetch_qs._cache_key()

filter_qs = Category.objects.filter(pk=1)
def do_filter_cache_key():
    filter_qs._cache_key()


def do_common_construct():
    return Category.objects.filter(pk=1).exclude(title__contains='Hi').order_by('title')[:20]

def do_common_inplace():
    return Category.objects.inplace() \
                   .filter(pk=1).exclude(title__contains='Hi').order_by('title')[:20]

common_qs = do_common_construct()
def do_common_cache_key():
    common_qs._cache_key()


def prepare_obj():
    return Category.objects.cache().get(pk=1)

def do_invalidate_obj(obj):
    invalidate_obj(obj)

def do_save_obj(obj):
    obj.save()


### Complex queryset

from django.db.models import Q

def do_complex_construct():
    return Post.objects.filter(id__gt=1, title='Hi').exclude(category__in=[10, 20]) \
                       .filter(Q(id__range=(10, 20)) | ~Q(title__contains='abc'))

def do_complex_inplace():
    return Post.objects.inplace()                                                   \
                       .filter(id__gt=1, title='Hi').exclude(category__in=[10, 20]) \
                       .filter(Q(id__range=(10, 20)) | ~Q(title__contains='abc'))

complex_qs = do_complex_construct()
def do_complex_cache_key():
    return complex_qs._cache_key()


### More invalidation

def prepare_cache():
    def _variants(*args, **kwargs):
        qs = Extra.objects.cache().filter(*args, **kwargs)
        qs.count()
        list(qs)
        list(qs[:2])
        list(qs.values())

    _variants(pk=1)
    _variants(post=1)
    _variants(tag=5)
    _variants(to_tag=10)

    _variants(pk=1, post=1)
    _variants(pk=1, tag=5)
    _variants(post=1, tag=5)

    _variants(pk=1, post=1, tag=5)
    _variants(pk=1, post=1, to_tag=10)

    _variants(Q(pk=1) | Q(tag=5))
    _variants(Q(pk=1) | Q(tag=1))
    _variants(Q(pk=1) | Q(tag=2))
    _variants(Q(pk=1) | Q(tag=3))
    _variants(Q(pk=1) | Q(tag=4))

    return Extra.objects.cache().get(pk=1)

def do_invalidate_model(obj):
    invalidate_model(obj.__class__)


TESTS = [
    ('pickle', {'run': do_pickle}),
    ('unpickle', {'run': do_unpickle}),

    ('get_no_cache', {'run': do_get_no_cache}),
    ('get_hit', {'prepare_once': do_get, 'run': do_get}),
    ('get_miss', {'prepare': invalidate_get, 'run': do_get}),

    ('count_no_cache', {'run': do_count_no_cache}),
    ('count_hit', {'prepare_once': do_count, 'run': do_count}),
    ('count_miss', {'prepare': invalidate_count, 'run': do_count}),

    ('fetch_construct', {'run': do_fetch_construct}),
    ('fetch_no_cache', {'run': do_fetch_no_cache}),
    ('fetch_hit', {'prepare_once': do_fetch, 'run': do_fetch}),
    ('fetch_miss', {'prepare': invalidate_fetch, 'run': do_fetch}),
    ('fetch_cache_key', {'run': do_fetch_cache_key}),

    ('filter_cache_key', {'run': do_filter_cache_key}),
    ('common_construct', {'run': do_common_construct}),
    ('common_inplace', {'run': do_common_inplace}),
    ('common_cache_key', {'run': do_common_cache_key}),

    ('invalidate_obj', {'prepare': prepare_obj, 'run': do_invalidate_obj}),
    ('save_obj', {'prepare': prepare_obj, 'run': do_save_obj}),

    ('complex_construct', {'run': do_complex_construct}),
    ('complex_inplace', {'run': do_complex_inplace}),
    ('complex_cache_key', {'run': do_complex_cache_key}),

    ('big_invalidate', {'prepare': prepare_cache, 'run': do_invalidate_obj}),
    ('model_invalidate', {'prepare': prepare_cache, 'run': do_invalidate_model}),
]

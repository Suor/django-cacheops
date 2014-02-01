from cacheops import invalidate_obj
from cacheops.conf import redis_client
from .models import Category, Post


count_key = Category.objects.all()._cache_key(extra='count')
def invalidate_count():
    redis_client.delete(count_key)

def do_count():
    Category.objects.cache().count()

def do_count_no_cache():
    Category.objects.nocache().count()


fetch_key = Category.objects.all()._cache_key()
def invalidate_fetch():
    redis_client.delete(fetch_key)

def do_fetch():
    list(Category.objects.cache().all())

def do_fetch_no_cache():
    list(Category.objects.nocache().all())

def do_fetch_construct():
    Category.objects.all()


def prepare_obj():
    return Category.objects.cache().get(pk=1)

def do_invalidate_obj(obj):
    invalidate_obj(obj)

def do_save_obj(obj):
    obj.save()


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


TESTS = [
    ('count_no_cache', {'run': do_count_no_cache}),
    ('count_hit',  {'prepare_once': do_count, 'run': do_count}),
    ('count_miss', {'prepare': invalidate_count, 'run': do_count}),
    ('fetch_construct',  {'run': do_fetch_construct}),
    ('fetch_no_cache',  {'run': do_fetch_no_cache}),
    ('fetch_hit',  {'prepare_once': do_fetch, 'run': do_fetch}),
    ('fetch_miss', {'prepare': invalidate_fetch, 'run': do_fetch}),

    ('invalidate_obj', {'prepare': prepare_obj, 'run': do_invalidate_obj}),
    ('save_obj', {'prepare': prepare_obj, 'run': do_save_obj}),

    ('complex_construct', {'run': do_complex_construct}),
    ('complex_inplace', {'run': do_complex_inplace}),
    ('complex_cache_key', {'run': do_complex_cache_key}),
]

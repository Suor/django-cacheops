from cacheops import invalidate_all
from cacheops.conf import redis_client
from .models import Category, Post


count_key = Category.objects.all()._cache_key(extra='count')
def invalidate_count():
    redis_client.delete(count_key)

def do_count():
    return Category.objects.cache().count()

def do_count_no_cache():
    return Category.objects.nocache().count()


fetch_key = Category.objects.all()._cache_key()
def invalidate_fetch():
    redis_client.delete(fetch_key)

def do_fetch():
    return list(Category.objects.cache().all())

def do_fetch_no_cache():
    return list(Category.objects.nocache().all())

def do_fetch_construct():
    return Category.objects.all()


TESTS = [
    ('count_no_cache', {'run': do_count_no_cache}),
    ('count_hit',  {'prepare_once': do_count, 'run': do_count}),
    ('count_miss', {'prepare': invalidate_count, 'run': do_count}),
    ('fetch_construct',  {'run': do_fetch_construct}),
    ('fetch_no_cache',  {'run': do_fetch_no_cache}),
    ('fetch_hit',  {'prepare_once': do_fetch, 'run': do_fetch}),
    ('fetch_miss', {'prepare': invalidate_fetch, 'run': do_fetch}),
]

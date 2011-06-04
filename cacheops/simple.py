# -*- coding: utf-8 -*-
try:
    import cPickle as pickle
except ImportError:
    import pickle
from functools import wraps

from django.utils.hashcompat import md5_constructor
from django.core.cache import get_cache
from django.conf import settings

from cacheops.conf import redis_conn


__all__ = ('cache', 'cached', 'file_cache')
FILE_CACHE_URI = 'file://%s/tmp/django_cache' % settings.HOME_DIR


class BaseCache(object):
    """
    Кэширует переданные данные без инвалидации.
    """
    def cached(self, cache_key=None, timeout=None):
        """
        Декоратор кеширующий вызовы функции
        """
        def decorator(func):
            @wraps(func)
            def wrapper(*args, **kwargs):
                # Вычисляем ключ кеширования
                _cache_key = cache_key
                if _cache_key is None:
                    parts = (func.__module__, func.__name__, repr(args), repr(kwargs))
                    _cache_key = '%s' % md5_constructor('.'.join(parts)).hexdigest()
                _cache_key = 'c:%s' % _cache_key

                # Берём из кеша
                result = self.get(_cache_key)

                # Если кеш пуст, считаем по-честному и пишем в кеш
                if result is None:
                    result = func(*args, **kwargs)
                    self.set(_cache_key, result, timeout)

                return result
            return wrapper
        return decorator


class RedisCache(BaseCache):
    def __init__(self, conn):
        self.conn = conn

    def get(self, cache_key):
        data = self.conn.get(cache_key)
        if data is None:
            return None
        else:
            return pickle.loads(data)

    def set(self, cache_key, data, timeout=None):
        pickled_data = pickle.dumps(data, -1)
        if timeout is not None:
            self.conn.setex(cache_key, pickled_data, timeout)
        else:
            self.conn.set(cache_key, pickled_data)

cache = RedisCache(redis_conn)
cached = cache.cached


class DjangoCache(BaseCache):
    def __init__(self, uri):
        self.cache = get_cache(uri)

    def get(self, cache_key):
        return self.cache.get(cache_key)

    def set(self, cache_key, data, timeout=None):
        self.cache.set(cache_key, data, timeout)

file_cache = DjangoCache(FILE_CACHE_URI)

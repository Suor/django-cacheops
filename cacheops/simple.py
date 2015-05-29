# -*- coding: utf-8 -*-
import os, time
from functools import wraps
from .cross import pickle, md5hex

from django.conf import settings

from .conf import redis_client, handle_connection_failure
from .utils import func_cache_key, cached_view_fab


__all__ = ('cache', 'cached', 'cached_view', 'file_cache', 'CacheMiss')


class CacheMiss(Exception):
    pass

class CacheKey(str):
    @classmethod
    def make(cls, value, cache=None, timeout=None):
        self = CacheKey(value)
        self.cache = cache
        self.timeout = timeout
        return self

    def get(self):
        self.cache.get(self)

    def set(self, value):
        self.cache.set(self, value, self.timeout)

    def delete(self):
        self.cache.delete(self)

class BaseCache(object):
    """
    Simple cache with time-based invalidation
    """
    def _cached(self, timeout=None, extra=None, _get_key=None):
        """
        A decorator for caching function calls
        """
        def decorator(func):
            @wraps(func)
            def wrapper(*args, **kwargs):
                cache_key = 'c:' + _get_key(func, args, kwargs, extra)
                try:
                    result = self.get(cache_key)
                except CacheMiss:
                    result = func(*args, **kwargs)
                    self.set(cache_key, result, timeout)

                return result

            def invalidate(*args, **kwargs):
                cache_key = 'c:' + _get_key(func, args, kwargs, extra)
                self.delete(cache_key)
            wrapper.invalidate = invalidate

            def key(*args, **kwargs):
                cache_key = 'c:' + _get_key(func, args, kwargs, extra)
                return CacheKey.make(cache_key, cache=self, timeout=timeout)
            wrapper.key = key

            return wrapper
        return decorator

    def cached(self, timeout=None, extra=None):
        return self._cached(timeout, extra, _get_key=func_cache_key)

    def cached_view(self, timeout=None, extra=None):
        return cached_view_fab(self._cached)(timeout, extra)


class RedisCache(BaseCache):
    def __init__(self, conn):
        self.conn = conn

    def get(self, cache_key):
        data = self.conn.get(cache_key)
        if data is None:
            raise CacheMiss
        return pickle.loads(data)

    @handle_connection_failure
    def set(self, cache_key, data, timeout=None):
        pickled_data = pickle.dumps(data, -1)
        if timeout is not None:
            self.conn.setex(cache_key, timeout, pickled_data)
        else:
            self.conn.set(cache_key, pickled_data)

    @handle_connection_failure
    def delete(self, cache_key):
        self.conn.delete(cache_key)

cache = RedisCache(redis_client)
cached = cache.cached
cached_view = cache.cached_view


FILE_CACHE_DIR = getattr(settings, 'FILE_CACHE_DIR', '/tmp/cacheops_file_cache')
FILE_CACHE_TIMEOUT = getattr(settings, 'FILE_CACHE_TIMEOUT', 60*60*24*30)

class FileCache(BaseCache):
    """
    A file cache which fixes bugs and misdesign in django default one.
    Uses mtimes in the future to designate expire time. This makes unnecessary
    reading stale files.
    """
    def __init__(self, path, timeout=FILE_CACHE_TIMEOUT):
        self._dir = path
        self._default_timeout = timeout

    def _key_to_filename(self, key):
        """
        Returns a filename corresponding to cache key
        """
        digest = md5hex(key)
        return os.path.join(self._dir, digest[-2:], digest[:-2])

    def get(self, key):
        filename = self._key_to_filename(key)
        try:
            # Remove file if it's stale
            if time.time() >= os.stat(filename).st_mtime:
                self.delete(filename)
                raise CacheMiss

            with open(filename, 'rb') as f:
                return pickle.load(f)
        except (IOError, OSError, EOFError, pickle.PickleError):
            raise CacheMiss

    def set(self, key, data, timeout=None):
        filename = self._key_to_filename(key)
        dirname = os.path.dirname(filename)

        if timeout is None:
            timeout = self._default_timeout

        try:
            if not os.path.exists(dirname):
                os.makedirs(dirname)

            # Use open with exclusive rights to prevent data corruption
            f = os.open(filename, os.O_EXCL | os.O_WRONLY | os.O_CREAT)
            try:
                os.write(f, pickle.dumps(data, pickle.HIGHEST_PROTOCOL))
            finally:
                os.close(f)

            # Set mtime to expire time
            os.utime(filename, (0, time.time() + timeout))
        except (IOError, OSError):
            pass

    def delete(self, fname):
        try:
            os.remove(fname)
            # Trying to remove directory in case it's empty
            dirname = os.path.dirname(fname)
            os.rmdir(dirname)
        except (IOError, OSError):
            pass

file_cache = FileCache(FILE_CACHE_DIR)

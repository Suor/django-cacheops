import os
import time

from funcy import wraps

from .conf import settings
from .utils import get_cache_key, cached_view_fab, md5hex
from .redis import redis_client, handle_connection_failure
from .sharding import get_prefix


__all__ = ('cache', 'cached', 'cached_view', 'file_cache', 'CacheMiss', 'FileCache', 'RedisCache')


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
        self.cache._get(self)

    def set(self, value):
        self.cache._set(self, value, self.timeout)

    def delete(self):
        self.cache._delete(self)

class BaseCache(object):
    """
    Simple cache with time-based invalidation
    """
    def cached(self, timeout=None, extra=None):
        """
        A decorator for caching function calls
        """
        # Support @cached (without parentheses) form
        if callable(timeout):
            return self.cached()(timeout)

        def _get_key(func, args, kwargs):
            extra_val = extra(*args, **kwargs) if callable(extra) else extra
            return get_prefix(func=func) + 'c:' + get_cache_key(func, args, kwargs, extra_val)

        def decorator(func):
            @wraps(func)
            def wrapper(*args, **kwargs):
                if not settings.CACHEOPS_ENABLED:
                    return func(*args, **kwargs)

                cache_key = _get_key(func, args, kwargs)
                try:
                    result = self._get(cache_key)
                except CacheMiss:
                    result = func(*args, **kwargs)
                    self._set(cache_key, result, timeout)

                return result

            def invalidate(*args, **kwargs):
                self._delete(_get_key(func, args, kwargs))
            wrapper.invalidate = invalidate

            def key(*args, **kwargs):
                return CacheKey.make(_get_key(func, args, kwargs), cache=self, timeout=timeout)
            wrapper.key = key

            return wrapper
        return decorator

    def cached_view(self, timeout=None, extra=None):
        if callable(timeout):
            return self.cached_view()(timeout)
        return cached_view_fab(self.cached)(timeout=timeout, extra=extra)

    def get(self, cache_key):
        return self._get(get_prefix() + cache_key)

    def set(self, cache_key, data, timeout=None):
        self._set(get_prefix() + cache_key, data, timeout)

    def delete(self, cache_key):
        self._delete(get_prefix() + cache_key)


class RedisCache(BaseCache):
    def __init__(self, conn):
        self.conn = conn

    def _get(self, cache_key):
        data = self.conn.get(cache_key)
        if data is None:
            raise CacheMiss
        return settings.CACHEOPS_SERIALIZER.loads(data)

    @handle_connection_failure
    def _set(self, cache_key, data, timeout=None):
        pickled_data = settings.CACHEOPS_SERIALIZER.dumps(data)
        if timeout is not None:
            self.conn.setex(cache_key, timeout, pickled_data)
        else:
            self.conn.set(cache_key, pickled_data)

    @handle_connection_failure
    def _delete(self, cache_key):
        self.conn.delete(cache_key)

cache = RedisCache(redis_client)
cached = cache.cached
cached_view = cache.cached_view


class FileCache(BaseCache):
    """
    A file cache which fixes bugs and misdesign in django default one.
    Uses mtimes in the future to designate expire time. This makes unnecessary
    reading stale files.
    """
    def __init__(self, path, timeout=settings.FILE_CACHE_TIMEOUT):
        self._dir = path
        self._default_timeout = timeout

    def _key_to_filename(self, key):
        """
        Returns a filename corresponding to cache key
        """
        digest = md5hex(key)
        return os.path.join(self._dir, digest[-2:], digest[:-2])

    def _get(self, key):
        filename = self._key_to_filename(key)
        try:
            # Remove file if it's stale
            if time.time() >= os.stat(filename).st_mtime:
                self.delete(filename)
                raise CacheMiss

            with open(filename, 'rb') as f:
                return settings.CACHEOPS_SERIALIZER.load(f)
        except (IOError, OSError, EOFError):
            raise CacheMiss

    def _set(self, key, data, timeout=None):
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
                os.write(f, settings.CACHEOPS_SERIALIZER.dumps(data))
            finally:
                os.close(f)

            # Set mtime to expire time
            os.utime(filename, (0, time.time() + timeout))
        except (IOError, OSError):
            pass

    def _delete(self, fname):
        try:
            os.remove(fname)
            # Trying to remove directory in case it's empty
            dirname = os.path.dirname(fname)
            os.rmdir(dirname)
        except (IOError, OSError):
            pass

file_cache = FileCache(settings.FILE_CACHE_DIR)

# -*- coding: utf-8 -*-
try:
    import cPickle as pickle
except ImportError:
    import pickle
from functools import wraps
import os, time

from django.utils.hashcompat import md5_constructor
from django.conf import settings

from cacheops.conf import redis_conn


__all__ = ('cache', 'cached', 'file_cache')


class CacheMiss(Exception):
    pass


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

                try:
                    result = self.get(_cache_key)
                except CacheMiss:
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
            raise CacheMiss
        return pickle.loads(data)

    def set(self, cache_key, data, timeout=None):
        pickled_data = pickle.dumps(data, -1)
        if timeout is not None:
            self.conn.setex(cache_key, pickled_data, timeout)
        else:
            self.conn.set(cache_key, pickled_data)

cache = RedisCache(redis_conn)
cached = cache.cached


FILE_CACHE_MAX_ENTRIES = getattr(settings, 'FILE_CACHE_MAX_ENTRIES', 40000)
FILE_CACHE_TIMEOUT = getattr(settings, 'FILE_CACHE_TIMEOUT', 60*60)

class FileCache(BaseCache):
    def __init__(self, path, max_entries=FILE_CACHE_MAX_ENTRIES, timeout=FILE_CACHE_TIMEOUT):
        self._dir = path
        self._max_entries = max_entries
        self._default_timeout = timeout

    def _key_to_filename(self, key):
        """
        Возвращает имя файла, соответствующее переданному ключу кеша
        """
        digest = md5_constructor(key).hexdigest()
        return os.path.join(self._dir, digest[-2:], digest[:-2])

    def get(self, key):
        filename = self._key_to_filename(key)
        try:
            # Если файл старый, то удаляем его и симулируем промах
            if time.time() >= os.stat(filename).st_mtime:
                self.delete(filename)
                raise CacheMiss

            f = open(filename, 'rb')
            data = pickle.load(f)
            f.close()
            return data
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

            # Пишем эксклюзивно, чтобы избежать одновременной записи в файл и порчи данных
            f = os.open(filename, os.O_EXCL | os.O_WRONLY | os.O_CREAT)
            try:
                os.write(f, pickle.dumps(data, pickle.HIGHEST_PROTOCOL))
            finally:
                os.close(f)

            # mtime отмечает время устаревания, ставим его в будущее
            os.utime(filename, (0, time.time() + timeout))
        except (IOError, OSError):
            pass

    def delete(self, fname):
        try:
            os.remove(fname)
            # Пытаемся удалить директорию - вдруг пустая?
            dirname = os.path.dirname(fname)
            os.rmdir(dirname)
        except (IOError, OSError):
            pass

# Не используем os.path.join здесь потому как он сильно хитрожопый:
# если не первый аргумент начинается на /, то он понимается как абсолютный путь
cache_dir = settings.HOME_DIR + settings.FILE_CACHE_DIR
file_cache = FileCache(cache_dir)

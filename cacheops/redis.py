import warnings
from funcy.decorators import wraps

from contextlib import contextmanager

from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string

from funcy import decorator, identity, memoize, omit, LazyObject
import redis
from rediscluster import StrictRedisCluster
from redis.sentinel import Sentinel
from .conf import settings


if settings.CACHEOPS_DEGRADE_ON_FAILURE:
    @decorator
    def handle_connection_failure(call):
        try:
            return call()
        except redis.ConnectionError as e:
            warnings.warn("The cacheops cache is unreachable! Error: %s" % e, RuntimeWarning)
        except redis.TimeoutError as e:
            warnings.warn("The cacheops cache timed out! Error: %s" % e, RuntimeWarning)
else:
    handle_connection_failure = identity



def handle_timeout_exception(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except redis.TimeoutError as e:
            error_handler = settings.CACHEOPS_TIMEOUT_HANDLER
            if callable(error_handler):
                error_handler(e, *args, **kwargs)

            return None

    return wrapper


LOCK_TIMEOUT = 60


class CacheopsRedis(redis.StrictRedis):
    get = handle_connection_failure(redis.StrictRedis.get)
    # every function call this one, so better handle timeout at this level
    execute_command = handle_timeout_exception(redis.StrictRedis.execute_command)


    @contextmanager
    def getting(self, key, lock=False):
        if not lock:
            yield self.get(key)
        else:
            locked = False
            try:
                data = self._get_or_lock(key)
                locked = data is None
                yield data
            finally:
                if locked:
                    self._release_lock(key)

    @handle_connection_failure
    def _get_or_lock(self, key):
        self._lock = getattr(self, '_lock', self.register_script("""
            local locked = redis.call('set', KEYS[1], 'LOCK', 'nx', 'ex', ARGV[1])
            if locked then
                redis.call('del', KEYS[2])
            end
            return locked
        """))
        signal_key = key + ':signal'

        while True:
            data = self.get(key)
            if data is None:
                if self._lock(keys=[key, signal_key], args=[LOCK_TIMEOUT]):
                    return None
            elif data != b'LOCK':
                return data

            # No data and not locked, wait
            self.brpoplpush(signal_key, signal_key, timeout=LOCK_TIMEOUT)

    @handle_connection_failure
    def _release_lock(self, key):
        self._unlock = getattr(self, '_unlock', self.register_script("""
            if redis.call('get', KEYS[1]) == 'LOCK' then
                redis.call('del', KEYS[1])
            end
            redis.call('lpush', KEYS[2], 1)
            redis.call('expire', KEYS[2], 1)
        """))
        signal_key = key + ':signal'
        self._unlock(keys=[key, signal_key])


class CacheopsRedisCluster(StrictRedisCluster, CacheopsRedis):
    get = handle_connection_failure(StrictRedisCluster.get)
    # every function call this one, so better handle timeout at this level
    execute_command = handle_timeout_exception(StrictRedisCluster.execute_command)

    def __init__(self, *args, **kwargs):
        init_slot_cache = kwargs.get('init_slot_cache', True)
        super(CacheopsRedisCluster, self).__init__(*args, **kwargs)
        # lazy initialize nodes, so that if redis is downed before starting django, everything still fine
        if not init_slot_cache:
            self.refresh_table_asap = True

    def _handle_lock(self, keys, args):
        """
        Move old lua script to this function so that we can work with cluster mode
        """
        locked = self.set(keys[0], 'LOCK', ex=args[0], nx=True)
        if locked:
            self.delete(keys[1])

        return locked

    @handle_connection_failure
    def _get_or_lock(self, key):
        self._lock = getattr(self, '_lock', self._handle_lock)
        signal_key = key + ':signal'

        while True:
            data = self.get(key)
            if data is None:
                if self._lock(keys=[key, signal_key], args=[LOCK_TIMEOUT]):
                    return None
            elif data != b'LOCK':
                return data

            # No data and not locked, wait
            self.brpoplpush(signal_key, signal_key, timeout=LOCK_TIMEOUT)

    def _handle_release_lock(self, keys):
        if self.get(keys[0]) == 'LOCK':
            self.delete(keys[0])

        self.lpush(keys[1], 1)
        self.expire(keys[1], 1)

    @handle_connection_failure
    def _release_lock(self, key):
        """
        Move old lua script to this function so that we can work with cluster mode
        """
        self._unlock = getattr(self, '_unlock', self._handle_release_lock)
        signal_key = key + ':signal'
        self._unlock(keys=[key, signal_key])


@LazyObject
def redis_client():
    if settings.CACHEOPS_REDIS and settings.CACHEOPS_SENTINEL:
        raise ImproperlyConfigured("CACHEOPS_REDIS and CACHEOPS_SENTINEL are mutually exclusive")

    client_class = CacheopsRedisCluster if settings.CACHEOPS_CLUSTER_ENABLED else CacheopsRedis

    if settings.CACHEOPS_CLIENT_CLASS:
        client_class = import_string(settings.CACHEOPS_CLIENT_CLASS)

    if settings.CACHEOPS_SENTINEL:
        if not {'locations', 'service_name'} <= set(settings.CACHEOPS_SENTINEL):
            raise ImproperlyConfigured("Specify locations and service_name for CACHEOPS_SENTINEL")

        sentinel = Sentinel(
            settings.CACHEOPS_SENTINEL['locations'],
            **omit(settings.CACHEOPS_SENTINEL, ('locations', 'service_name', 'db')))
        return sentinel.master_for(
            settings.CACHEOPS_SENTINEL['service_name'],
            redis_class=client_class,
            db=settings.CACHEOPS_SENTINEL.get('db', 0)
        )

    redis_client = None

    # Allow client connection settings to be specified by a URL.
    if isinstance(settings.CACHEOPS_REDIS, str):
        redis_client = client_class.from_url(settings.CACHEOPS_REDIS)
        redis_client.socket_timeout = settings.CACHEOPS_REDIS_CONNECTION_TIMEOUT
        redis_client.socket_connect_timeout = settings.CACHEOPS_REDIS_CONNECTION_TIMEOUT
    else:
        kargs = {
            "socket_timeout": settings.CACHEOPS_REDIS_CONNECTION_TIMEOUT,
            "socket_connect_timeout": settings.CACHEOPS_REDIS_CONNECTION_TIMEOUT,
            **settings.CACHEOPS_REDIS
        }
        redis_client = client_class(**kargs)

    return redis_client


### Lua script loader

import re
import os.path

STRIP_RE = re.compile(r'TOSTRIP.*/TOSTRIP', re.S)

@memoize
def load_script(name, strip=False):
    filename = os.path.join(os.path.dirname(__file__), 'lua/%s.lua' % name)
    with open(filename) as f:
        code = f.read()
    if strip:
        code = STRIP_RE.sub('', code)
    return redis_client.register_script(code)

@memoize
def load_script_cluster(name, strip=False):
    filename = os.path.join(os.path.dirname(__file__), 'cluster/lua/%s.lua' % name)
    with open(filename) as f:
        code = f.read()
    if strip:
        code = STRIP_RE.sub('', code)
    return redis_client.register_script(code)

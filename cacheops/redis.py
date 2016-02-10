from __future__ import absolute_import
import warnings

from funcy import decorator, identity, memoize
import redis
from django.core.exceptions import ImproperlyConfigured

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


class SafeRedis(redis.StrictRedis):
    get = handle_connection_failure(redis.StrictRedis.get)

    def get_with_ttl(self, name):
        txn = redis_client.pipeline()
        cache_data = redis_client.get(name)
        ttl = redis_client.ttl(name)
        txn.execute()
        return cache_data, ttl


class LazyRedis(object):
    def _setup(self):
        if not settings.CACHEOPS_REDIS:
            raise ImproperlyConfigured('You must specify CACHEOPS_REDIS setting to use cacheops')

        Redis = SafeRedis if (
            settings.CACHEOPS_DEGRADE_ON_FAILURE or
            settings.CACHEOPS_SEND_AGE
        ) else redis.StrictRedis
        client = Redis(**settings.CACHEOPS_REDIS)

        object.__setattr__(self, '__class__', client.__class__)
        object.__setattr__(self, '__dict__', client.__dict__)

    def __getattr__(self, name):
        self._setup()
        return getattr(self, name)

    def __setattr__(self, name, value):
        self._setup()
        return setattr(self, name, value)

redis_client = LazyRedis()


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

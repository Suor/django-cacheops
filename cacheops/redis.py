from __future__ import absolute_import
import warnings
import six
import sys
import traceback
import logging
from funcy import decorator, identity, memoize
import redis
from django.core.exceptions import ImproperlyConfigured
import random
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
        except Exception as e:
            warnings.warn("".join(traceback.format_exception(*sys.exc_info())))
else:
    handle_connection_failure = identity


class SafeRedis(redis.StrictRedis):
    get = handle_connection_failure(redis.StrictRedis.get)

    """ Handles failover of AWS elasticache
    """
    def execute_command(self, *args, **options):
        try:
            return super(SafeRedis, self).execute_command(*args, **options)
        except redis.ResponseError as e:
            if "READONLY" not in e.message:
                raise
            connection = self.connection_pool.get_connection(args[0], **options)
            connection.disconnect()
            warnings.warn("Primary probably failed over, reconnecting")
            return super(SafeRedis, self).execute_command(*args, **options)

class LazyRedis(object):
    def _setup(self):
        if not settings.CACHEOPS_REDIS:
            raise ImproperlyConfigured('You must specify CACHEOPS_REDIS setting to use cacheops')

        Redis = SafeRedis if settings.CACHEOPS_DEGRADE_ON_FAILURE else redis.StrictRedis

        # Allow client connection settings to be specified by a URL.
        if isinstance(settings.CACHEOPS_REDIS, six.string_types):
            client = Redis.from_url(settings.CACHEOPS_REDIS)
        else:
            client = Redis(**settings.CACHEOPS_REDIS)

        object.__setattr__(self, '__class__', client.__class__)
        object.__setattr__(self, '__dict__', client.__dict__)

    def __getattr__(self, name):
        self._setup()
        return getattr(self, name)

    def __setattr__(self, name, value):
        self._setup()
        return setattr(self, name, value)


CacheopsRedis = SafeRedis if settings.CACHEOPS_DEGRADE_ON_FAILURE else redis.StrictRedis
try:
    # the conf could be a list of string
    # list would look like: ["redis://cache-001:6379/1", "redis://cache-002:6379/2"]
    # string would be: "redis://cache-001:6379/1,redis://cache-002:6379/2"
    redis_replica_conf = settings.CACHEOPS_REDIS_REPLICA
    if isinstance(settings.CACHEOPS_REDIS, six.string_types):
        redis_replicas = map(redis.StrictRedis.from_url, redis_replica_conf.split(','))
    else:
        redis_replicas = map(redis.StrictRedis.from_url, redis_replica_conf)
except AttributeError as err:
    redis_client = LazyRedis()
else:
    class ReplicaProxyRedis(CacheopsRedis):
        """ Proxy `get` calls to redis replica.
        """
        def get(self, *args, **kwargs):
            try:
                redis_replica = random.choice(redis_replicas)
                return redis_replica.get(*args, **kwargs)
            except redis.ConnectionError:
                return super(ReplicaProxyRedis, self).get(*args, **kwargs)

    redis_client = ReplicaProxyRedis(**settings.CACHEOPS_REDIS)

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

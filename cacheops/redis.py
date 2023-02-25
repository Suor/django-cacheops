import warnings

from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string

from funcy import decorator, identity, memoize, omit, LazyObject
import redis
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


@LazyObject
def redis_client():
    if settings.CACHEOPS_REDIS and settings.CACHEOPS_SENTINEL:
        raise ImproperlyConfigured("CACHEOPS_REDIS and CACHEOPS_SENTINEL are mutually exclusive")

    client_class = redis.StrictRedis
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

    # Allow client connection settings to be specified by a URL.
    if isinstance(settings.CACHEOPS_REDIS, str):
        return client_class.from_url(settings.CACHEOPS_REDIS)
    else:
        return client_class(**settings.CACHEOPS_REDIS)


### Lua script loader

import os.path


@memoize
def load_script(name):
    filename = os.path.join(os.path.dirname(__file__), 'lua/%s.lua' % name)
    with open(filename) as f:
        code = f.read()
    return redis_client.register_script(code)

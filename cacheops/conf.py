# -*- coding: utf-8 -*-
import warnings
import six
import redis
from funcy import memoize, decorator, identity, merge

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


CACHEOPS_REDIS = getattr(settings, 'CACHEOPS_REDIS', None)
CACHEOPS_DEFAULTS = getattr(settings, 'CACHEOPS_DEFAULTS', {})
CACHEOPS = getattr(settings, 'CACHEOPS', {})
CACHEOPS_LRU = getattr(settings, 'CACHEOPS_LRU', False)
CACHEOPS_DEGRADE_ON_FAILURE = getattr(settings, 'CACHEOPS_DEGRADE_ON_FAILURE', False)

FILE_CACHE_DIR = getattr(settings, 'FILE_CACHE_DIR', '/tmp/cacheops_file_cache')
FILE_CACHE_TIMEOUT = getattr(settings, 'FILE_CACHE_TIMEOUT', 60*60*24*30)

ALL_OPS = {'get', 'fetch', 'count', 'exists'}


# Support DEGRADE_ON_FAILURE
if CACHEOPS_DEGRADE_ON_FAILURE:
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


class LazyRedis(object):
    def _setup(self):
        if not CACHEOPS_REDIS:
            raise ImproperlyConfigured('You must specify CACHEOPS_REDIS setting to use cacheops')

        client = (SafeRedis if CACHEOPS_DEGRADE_ON_FAILURE else redis.StrictRedis)(**CACHEOPS_REDIS)

        object.__setattr__(self, '__class__', client.__class__)
        object.__setattr__(self, '__dict__', client.__dict__)

    def __getattr__(self, name):
        self._setup()
        return getattr(self, name)

    def __setattr__(self, name, value):
        self._setup()
        return setattr(self, name, value)

redis_client = LazyRedis()


@memoize
def prepare_profiles():
    """
    Prepares a dict 'app.model' -> profile, for use in model_profile()
    """
    profile_defaults = {
        'ops': (),
        'local_get': False,
        'db_agnostic': True,
    }
    profile_defaults.update(CACHEOPS_DEFAULTS)

    model_profiles = {}
    for app_model, profile in CACHEOPS.items():
        if profile is None:
            model_profiles[app_model] = None
            continue

        model_profiles[app_model] = mp = merge(profile_defaults, profile)
        if mp['ops'] == 'all':
            mp['ops'] = ALL_OPS
        # People will do that anyway :)
        if isinstance(mp['ops'], six.string_types):
            mp['ops'] = {mp['ops']}
        mp['ops'] = set(mp['ops'])

        if 'timeout' not in mp:
            raise ImproperlyConfigured(
                'You must specify "timeout" option in "%s" CACHEOPS profile' % app_model)

    return model_profiles

@memoize
def model_profile(model):
    """
    Returns cacheops profile for a model
    """
    model_profiles = prepare_profiles()

    app = model._meta.app_label
    model_name = model._meta.model_name
    for guess in ('%s.%s' % (app, model_name), '%s.*' % app, '*.*'):
        if guess in model_profiles:
            return model_profiles[guess]
    else:
        return None

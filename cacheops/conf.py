# -*- coding: utf-8 -*-
import six
from funcy import memoize, merge

from django.conf import settings as base_settings
from django.core.exceptions import ImproperlyConfigured


ALL_OPS = {'get', 'fetch', 'count', 'exists'}


class Settings(object):
    CACHEOPS_ENABLED = True
    CACHEOPS_REDIS = {}
    CACHEOPS_DEFAULTS = {}
    CACHEOPS = {}
    CACHEOPS_LRU = False
    CACHEOPS_DEGRADE_ON_FAILURE = False
    FILE_CACHE_DIR = '/tmp/cacheops_file_cache'
    FILE_CACHE_TIMEOUT = 60*60*24*30

    def __getattribute__(self, name):
        if hasattr(base_settings, name):
            return getattr(base_settings, name)
        return object.__getattribute__(self, name)

settings = Settings()


@memoize
def prepare_profiles():
    """
    Prepares a dict 'app.model' -> profile, for use in model_profile()
    """
    profile_defaults = {
        'ops': (),
        'local_get': False,
        'db_agnostic': True,
        'write_only': False,
    }
    profile_defaults.update(settings.CACHEOPS_DEFAULTS)

    model_profiles = {}
    for app_model, profile in settings.CACHEOPS.items():
        if profile is None:
            model_profiles[app_model.lower()] = None
            continue

        model_profiles[app_model.lower()] = mp = merge(profile_defaults, profile)
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


def model_profile(model):
    """
    Returns cacheops profile for a model
    """
    if model_is_fake(model):
        return None

    model_profiles = prepare_profiles()

    app = model._meta.app_label
    model_name = model._meta.model_name
    for guess in ('%s.%s' % (app, model_name), '%s.*' % app, '*.*'):
        if guess in model_profiles:
            return model_profiles[guess]
    else:
        return None


def model_is_fake(model):
    return model.__module__ == '__fake__'

# -*- coding: utf-8 -*-
import six
from funcy import memoize, merge, namespace

from django.conf import settings as base_settings
from django.core.exceptions import ImproperlyConfigured
from django.core.signals import setting_changed
from django.utils.module_loading import import_string


ALL_OPS = {'get', 'fetch', 'count', 'aggregate', 'exists'}


class Defaults(namespace):
    CACHEOPS_ENABLED = True
    CACHEOPS_REDIS = {}
    CACHEOPS_DEFAULTS = {}
    CACHEOPS = {}
    CACHEOPS_PREFIX = lambda query: ''
    CACHEOPS_LRU = False
    CACHEOPS_DEGRADE_ON_FAILURE = False
    FILE_CACHE_DIR = '/tmp/cacheops_file_cache'
    FILE_CACHE_TIMEOUT = 60*60*24*30


class Settings(object):
    def __getattr__(self, name):
        res = getattr(base_settings, name, getattr(Defaults, name))
        if name == 'CACHEOPS_PREFIX':
            res = res if callable(res) else import_string(res)
        # Save to dict to speed up next access, __getattr__ won't be called
        self.__dict__[name] = res
        return res

settings = Settings()
setting_changed.connect(lambda setting, **kw: settings.__dict__.pop(setting, None), weak=False)


@memoize
def prepare_profiles():
    """
    Prepares a dict 'app.model' -> profile, for use in model_profile()
    """
    profile_defaults = {
        'ops': (),
        'local_get': False,
        'db_agnostic': True,
        'lock': False,
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
    if model.__module__ == '__fake__':
        return None

    model_profiles = prepare_profiles()

    app = model._meta.app_label.lower()
    model_name = model._meta.model_name
    for guess in ('%s.%s' % (app, model_name), '%s.*' % app, '*.*'):
        if guess in model_profiles:
            return model_profiles[guess]
    else:
        return None

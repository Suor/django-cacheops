# -*- coding: utf-8 -*-
import six
from funcy import memoize, merge

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
    # module_name is fallback for Django 1.5-
    model_name = getattr(model._meta, 'model_name', None) or model._meta.module_name
    app_model = '%s.%s' % (app, model_name)
    for guess in (app_model, '%s.*' % app, '*.*'):
        if guess in model_profiles:
            return model_profiles[guess]
    else:
        return None

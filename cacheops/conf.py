# -*- coding: utf-8 -*-
import six
from funcy import memoize, merge

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


ALL_OPS = {'get', 'fetch', 'count', 'exists'}
LRU = getattr(settings, 'CACHEOPS_LRU', False)
DEGRADE_ON_FAILURE = getattr(settings, 'CACHEOPS_DEGRADE_ON_FAILURE', False)

try:
    REDIS_CONF = settings.CACHEOPS_REDIS
except AttributeError:
    raise ImproperlyConfigured('You must specify CACHEOPS_REDIS setting to use cacheops')

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
    if hasattr(settings, 'CACHEOPS_DEFAULTS'):
        profile_defaults.update(settings.CACHEOPS_DEFAULTS)

    model_profiles = {}
    ops = getattr(settings, 'CACHEOPS', {})
    for app_model, profile in ops.items():
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

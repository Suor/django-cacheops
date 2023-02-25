from importlib import import_module
from funcy import memoize, merge

from django.conf import settings as base_settings
from django.core.exceptions import ImproperlyConfigured
from django.core.signals import setting_changed


ALL_OPS = {'get', 'fetch', 'count', 'aggregate', 'exists'}


class Defaults:
    CACHEOPS_ENABLED = True
    CACHEOPS_REDIS = {}
    CACHEOPS_DEFAULTS = {}
    CACHEOPS = {}
    CACHEOPS_PREFIX = lambda query: ''
    CACHEOPS_INSIDEOUT = False
    CACHEOPS_CLIENT_CLASS = None
    CACHEOPS_DEGRADE_ON_FAILURE = False
    CACHEOPS_SENTINEL = {}
    # NOTE: we don't use this fields in invalidator conditions since their values could be very long
    #       and one should not filter by their equality anyway.
    CACHEOPS_SKIP_FIELDS = "FileField", "TextField", "BinaryField", "JSONField"
    CACHEOPS_LONG_DISJUNCTION = 8
    CACHEOPS_SERIALIZER = 'pickle'

    FILE_CACHE_DIR = '/tmp/cacheops_file_cache'
    FILE_CACHE_TIMEOUT = 60*60*24*30


class Settings(object):
    def __getattr__(self, name):
        res = getattr(base_settings, name, getattr(Defaults, name))
        if name in ['CACHEOPS_PREFIX', 'CACHEOPS_SERIALIZER']:
            res = import_string(res) if isinstance(res, str) else res

        # Convert old list of classes to list of strings
        if name == 'CACHEOPS_SKIP_FIELDS':
            res = [f if isinstance(f, str) else f.get_internal_type(res) for f in res]

        # Save to dict to speed up next access, __getattr__ won't be called
        self.__dict__[name] = res
        return res

settings = Settings()
setting_changed.connect(lambda setting, **kw: settings.__dict__.pop(setting, None), weak=False)


def import_string(path):
    if "." in path:
        module, attr = path.rsplit(".", 1)
        return getattr(import_module(module), attr)
    else:
        return import_module(path)


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
        if isinstance(mp['ops'], str):
            mp['ops'] = {mp['ops']}
        mp['ops'] = set(mp['ops'])

        if 'timeout' not in mp:
            raise ImproperlyConfigured(
                'You must specify "timeout" option in "%s" CACHEOPS profile' % app_model)
        if not isinstance(mp['timeout'], int):
            raise ImproperlyConfigured(
                '"timeout" option in "%s" CACHEOPS profile should be an integer' % app_model)

    return model_profiles


def model_profile(model):
    """
    Returns cacheops profile for a model
    """
    # Django migrations these fake models, we don't want to cache them
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

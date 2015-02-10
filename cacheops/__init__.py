VERSION = (2, 3, 0)
__version__ = '.'.join(map(str, VERSION if VERSION[-1] else VERSION[:2]))


import django
from django.conf import settings


FAKE = getattr(settings, 'CACHEOPS_FAKE', False)
if not FAKE:
    from .simple import *
    from .query import *
    from .invalidation import *
    if django.VERSION >= (1, 4):
        from .templatetags.cacheops import *
else:
    from .fake import *


# Use app config for initialization in Django 1.7+
if django.VERSION >= (1, 7):
    from django.apps import AppConfig

    class CacheopsConfig(AppConfig):
        name = 'cacheops'

        def ready(self):
            install_cacheops()

    default_app_config = 'cacheops.CacheopsConfig'
else:
    install_cacheops()

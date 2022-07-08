__version__ = '6.1'
VERSION = tuple(map(int, __version__.split('.')))

import django
from .simple import *  # noqa
from .query import *  # noqa
from .invalidation import *  # noqa
from .templatetags.cacheops import *  # noqa


if django.VERSION < (3, 2):
    default_app_config = "cacheops.apps.CacheopsConfig"

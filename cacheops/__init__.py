__version__ = '5.0.1'
VERSION = tuple(map(int, __version__.split('.')))

import django
from .simple import *
from .query import *
from .invalidation import *
from .templatetags.cacheops import *

if django.VERSION < (3, 2):
    default_app_config = "cacheops.apps.CacheopsConfig"

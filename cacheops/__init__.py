__version__ = '7.1'
VERSION = tuple(map(int, __version__.split('.')))

from django.apps import AppConfig
from .simple import *  # noqa
from .query import *  # noqa
from .invalidation import *  # noqa
from .reaper import *  # noqa
from .templatetags.cacheops import *  # noqa
from .transaction import *  # noqa

class CacheopsConfig(AppConfig):
    name = 'cacheops'

    def ready(self):
        install_cacheops()
        install_cacheops_transaction_support()

default_app_config = 'cacheops.CacheopsConfig'

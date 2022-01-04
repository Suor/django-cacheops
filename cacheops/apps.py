from django.apps import AppConfig

from cacheops.query import install_cacheops
from cacheops.transaction import install_cacheops_transaction_support


class CacheopsConfig(AppConfig):
    name = 'cacheops'

    def ready(self):
        install_cacheops()
        install_cacheops_transaction_support()

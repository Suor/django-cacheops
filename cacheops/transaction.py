import json

try:
    from collections import ChainMap
except ImportError:
    from chainmap import ChainMap

from django.conf import settings
try:
    from django.db.transaction import Atomic, get_connection
except ImportError:
    # django is too old for this.
    Atomic = None
    get_connection = None

from .conf import LRU
from .utils import load_script
from .cross import pickle

class AtomicMixIn(object):
    def __enter__(self):
        if getattr(settings, 'CACHEOPS_RESPECT_ATOMIC', False):
            connection = get_connection(self.using)
            if not connection.in_atomic_block:
                # outer most atomic block.
                # setup our local cache
                Atomic.thread_local.cacheops_transaction_cache = ChainMap()
            else:
                # new inner atomic block
                # add a 'context' to our local cache.
                Atomic.thread_local.cacheops_transaction_cache.maps.append({})
        self._no_monkey.__enter__(self)

    def __exit__(self, exc_type, exc_value, traceback):
        self._no_monkey.__exit__(self, exc_type, exc_value, traceback)
        if getattr(settings, 'CACHEOPS_RESPECT_ATOMIC', False):
            connection = get_connection(self.using)
            commit = not connection.closed_in_transaction and\
                          exc_type is None and\
                          not connection.needs_rollback
            if not connection.in_atomic_block:
                # exit outer most atomic block.
                if commit:
                    # push the transaction's keys to redis
                    for key, value in Atomic.thread_local.cacheops_transaction_cache.items():
                        load_script('cache_thing', LRU)(
                            keys=[key],
                            args=[
                                pickle.dumps(value['data'], -1),
                                json.dumps(value['cond_dnfs'], default=str),
                                value['timeout']
                            ]
                        )
                del Atomic.thread_local.cacheops_transaction_cache
            else:
                # exit inner atomic block
                context = Atomic.thread_local.cacheops_transaction_cache.maps.pop(0)
                if commit:
                    # mash the save points context into the outer context.
                    Atomic.thread_local.cacheops_transaction_cache.maps[0].update(context)

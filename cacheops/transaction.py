import json
from threading import local

try:
    from collections import ChainMap
except ImportError:
    from chainmap import ChainMap

from django.conf import settings
from django.db import transaction, DEFAULT_DB_ALIAS

from .conf import LRU
from .utils import load_script
from .cross import pickle


class Atomic(transaction.Atomic):
    thread_local = local()

    def __enter__(self):
        if getattr(settings, 'CACHEOPS_ATOMIC_REQUESTS', False):
            connection = transaction.get_connection(self.using)
            if not connection.in_atomic_block:
                # outer most atomic block.
                # setup our local cache
                Atomic.thread_local.cache = ChainMap()
            else:
                # new inner atomic block
                # add a 'context' to our local cache.
                Atomic.thread_local.cache.maps.append({})
        super(Atomic, self).__enter__()

    def __exit__(self, exc_type, exc_value, traceback):
        super(Atomic, self).__exit__(exc_type, exc_value, traceback)
        if getattr(settings, 'CACHEOPS_ATOMIC_REQUESTS', False):
            connection = transaction.get_connection(self.using)
            commit = not connection.closed_in_transaction and\
                          exc_type is None and\
                          not connection.needs_rollback
            if not connection.in_atomic_block:
                # exit outer most atomic block.
                if commit:
                    # push the transaction's keys to redis
                    for key, value in Atomic.thread_local.cache.items():
                        load_script('cache_thing', LRU)(
                            keys=[key],
                            args=[
                                pickle.dumps(value['data'], -1),
                                json.dumps(value['cond_dnfs'], default=str),
                                value['timeout']
                            ]
                        )
                del Atomic.thread_local.cache
            else:
                # exit inner atomic block
                context = Atomic.thread_local.cache.maps.pop(0)
                if commit:
                    # mash the save points context into the outer context.
                    Atomic.thread_local.cache.maps[0].update(context)


def atomic(using=None, savepoint=True):
    # Bare decorator: @atomic -- although the first argument is called
    # `using`, it's actually the function being decorated.
    if callable(using):
        return Atomic(DEFAULT_DB_ALIAS, savepoint)(using)
    # Decorator: @atomic(...) or context manager: with atomic(...): ...
    else:
        return Atomic(using, savepoint)

transaction.original_atomic = transaction.atomic
transaction.OriginalAtomic = transaction.Atomic
transaction.atomic = atomic
transaction.Atomic = Atomic

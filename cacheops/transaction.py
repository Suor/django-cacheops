# -*- coding: utf-8 -*-
import threading
from django.db.transaction import get_connection, Atomic
from funcy import wraps, once, is_list
from funcy.py2 import ikeep, iflatten

from .utils import monkey_mix

__all__ = ('in_transaction', 'queue_when_in_transaction', 'install_cacheops_transaction_support')


class TransactionQueue(threading.local):
    """
    `_queue` is a list of lists of of arbitrary depth. it represents a queue of items, grouped by
     nested contexts. non-list elements at each level are items in the queue. list elements are
     considered contexts. the latest context is the deepest list who's right most boundary is also
     the right most boundary of the outer most context. `None` items are added to the list, in order
     to make second latest contexts the new latest context (when an inner most context ends). These
     `None` objects are not return on __iter__.

     You probably want to sub class this class to override commit_transaction, or in alternate cases
      rollback_transaction.
    """
    def __init__(self, *args, **kwargs):
        super(TransactionQueue, self).__init__(*args, **kwargs)
        self.in_transaction = False
        self._queue = []

    def begin(self):
        if self.in_transaction:
            # savepoint
            latest_context = self._queue
            while latest_context and isinstance(latest_context[-1], list):
                latest_context = latest_context[-1]
            latest_context.append([])
        else:
            # transaction
            self._queue = []
            self.in_transaction = True

    def commit(self):
        if self._queue and isinstance(self._queue[-1], list):
            # savepoint
            previous_context = None
            latest_context = self._queue
            while latest_context and isinstance(latest_context[-1], list):
                previous_context = latest_context
                latest_context = latest_context[-1]
            previous_context.append(None)
        else:
            # transaction
            for func, args, kwargs in self:
                func(*args, **kwargs)
            self.in_transaction = False
            self._queue = []

    def rollback(self):
        if self._queue and isinstance(self._queue[-1], list):
            # savepoint
            previous_context = None
            latest_context = self._queue
            while latest_context and isinstance(latest_context[-1], list):
                previous_context = latest_context
                latest_context = latest_context[-1]
            assert(previous_context is not None)
            previous_context.pop()
        else:
            # transaction
            self.in_transaction = False
            self._queue = None

    def append(self, item):
        latest_context = self._queue
        while latest_context and isinstance(latest_context[-1], list):
            latest_context = latest_context[-1]
        latest_context.append(item)

    def __iter__(self):
        return ikeep(iflatten(self._queue, follow=is_list))

_transaction_queue = TransactionQueue()


def in_transaction():
    return _transaction_queue.in_transaction


def queue_when_in_transaction(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if _transaction_queue.in_transaction:
            _transaction_queue.append((func, args, kwargs))
        else:
            func(*args, **kwargs)
    return wrapper


class AtomicMixIn(object):
    def __enter__(self):
        _transaction_queue.begin()
        self._no_monkey.__enter__(self)

    def __exit__(self, exc_type, exc_value, traceback):
        self._no_monkey.__exit__(self, exc_type, exc_value, traceback)
        connection = get_connection(self.using)
        if not connection.closed_in_transaction and exc_type is None and \
                not connection.needs_rollback:
            _transaction_queue.commit()
        else:
            _transaction_queue.rollback()


@once
def install_cacheops_transaction_support():
    monkey_mix(Atomic, AtomicMixIn)

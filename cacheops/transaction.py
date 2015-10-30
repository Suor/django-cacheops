# -*- coding: utf-8 -*-
import threading
from django.db.transaction import get_connection, Atomic
from funcy import wraps, once

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
        self._queue.append([])
        if not self.in_transaction:
            # transaction
            self.in_transaction = True

    def commit(self):
        context = self._queue.pop()
        if self._queue:
            # savepoint
            self._queue[-1].extend(context)
        else:
            # transaction
            for func, args, kwargs in context:
                func(*args, **kwargs)
            self.in_transaction = False

    def rollback(self):
        self._queue.pop()
        if not self._queue:
            # transaction
            self.in_transaction = False

    def append(self, item):
        self._queue[-1].append(item)

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

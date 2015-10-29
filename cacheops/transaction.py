# -*- coding: utf-8 -*-
import threading
from django.db.transaction import get_connection, Atomic
from funcy import wraps, once

from .utils import monkey_mix

__all__ = ('in_transaction', 'queue_when_in_transaction', 'install_cacheops_transaction_support')

_marker = object()


def _find_latest_context(contexts):
    if not contexts:
        return contexts
    latest = contexts[-1]
    if isinstance(latest, list):
        return_value = _find_latest_context(latest)
        if return_value is not None:
            return return_value
        return latest
    return None


def _find_second_latest_context(contexts):
    if not contexts:
        return contexts
    latest = contexts[-1]
    if isinstance(latest, list):
        return_value = _find_second_latest_context(latest)
        if return_value is _marker:
            return latest
        if return_value is not None:
            return return_value
        return _marker
    return None


def _drop_latest_context(contexts):
    if not contexts:
        return True
    latest = contexts[-1]
    if isinstance(latest, list):
        return_value = _drop_latest_context(latest)
        if return_value:
            contexts.pop()
            return False
        return return_value
    return True


def _flatten_and_ignore_none(items):
    """
    returns all non list items in a list of lists of of arbitrary depth at a single depth,
     ignoring non list items that are `None`.
    """
    for item in items:
        if isinstance(item, list):
            # todo: use yield from in python 3
            for x in _flatten_and_ignore_none(item):
                yield x
        elif item is not None:
            yield item


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
    def __init__(self):
        self._queue = None

    def start_transaction(self):
        self._queue = []

    def commit_transaction(self):
        self._queue = None

    def rollback_transaction(self):
        self._queue = None

    def start_savepoint(self):
        latest_context = _find_latest_context(self._queue)
        if latest_context is None:
            latest_context = self._queue
        latest_context.append([])

    def commit_savepoint(self):
        second_latest = _find_second_latest_context(self._queue)
        if second_latest is None or second_latest is _marker:
            second_latest = self._queue
        second_latest.append(None)

    def rollback_savepoint(self):
        return_value = _drop_latest_context(self._queue)
        if return_value:
            raise ValueError('Trying to drop the top most context.')

    def in_transaction(self):
        return self._queue is not None

    def append(self, item):
        latest_context = _find_latest_context(self._queue)
        if latest_context is None:
            latest_context = self._queue
        latest_context.append(item)

    def __iter__(self):
        return _flatten_and_ignore_none(self._queue)


class FunctionQueue(TransactionQueue):
    """
    items appended to this queue are assumed to be queued functions, in the form of:
        {
            'func': <some callable>
            'args': [],
            'kwargs': {}
        }
    these items are executed in order at transaction commit time.
    """
    def commit_transaction(self):
        for item in self:
            item['func'](*item['args'], **item['kwargs'])
        super(FunctionQueue, self).commit_transaction()

_function_queue = FunctionQueue()


def in_transaction():
    return _function_queue.in_transaction()


def queue_when_in_transaction(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if _function_queue.in_transaction():
            _function_queue.append({
                'func': func,
                'args': args,
                'kwargs': kwargs
            })
        else:
            func(*args, **kwargs)
    return wrapper


class AtomicMixIn(object):
    def __enter__(self):
        connection = get_connection(self.using)
        if not connection.in_atomic_block:
            _function_queue.start_transaction()
        else:
            _function_queue.start_savepoint()
        self._no_monkey.__enter__(self)

    def __exit__(self, exc_type, exc_value, traceback):
        self._no_monkey.__exit__(self, exc_type, exc_value, traceback)
        connection = get_connection(self.using)
        commit = not connection.closed_in_transaction and \
                      exc_type is None and \
                      not connection.needs_rollback
        if not connection.in_atomic_block:
            if commit:
                _function_queue.commit_transaction()
            else:
                _function_queue.rollback_transaction()
        else:
            if commit:
                _function_queue.commit_savepoint()
            else:
                _function_queue.rollback_savepoint()


@once
def install_cacheops_transaction_support():
    monkey_mix(Atomic, AtomicMixIn)

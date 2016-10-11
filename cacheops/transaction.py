# -*- coding: utf-8 -*-
import threading
from funcy import wraps, once
from django.db.transaction import get_connection, Atomic

from .utils import monkey_mix


__all__ = ('uncommited_changes', 'mark_transaction_dirty',
           'queue_when_in_transaction', 'install_cacheops_transaction_support')


class TransactionState(threading.local):
    def __init__(self, *args, **kwargs):
        super(TransactionState, self).__init__(*args, **kwargs)
        self._stack = []

    def begin(self):
        parent_dirty = self.is_dirty()
        self._stack.append({'success_cbs': [], 'dirty': parent_dirty})

    def commit(self):
        context = self._stack.pop()
        if self._stack:
            # savepoint
            self._stack[-1]['success_cbs'].extend(context['success_cbs'])
            if context['dirty']:
                self.mark_dirty()
        else:
            # transaction
            for func, args, kwargs in context['success_cbs']:
                func(*args, **kwargs)

    def rollback(self):
        self._stack.pop()

    def add_success_callback(self, func, args, kwargs):
        self._stack[-1]['success_cbs'].append((func, args, kwargs))

    def mark_dirty(self):
        self._stack[-1]['dirty'] = True

    def is_dirty(self):
        return self.in_transaction() and self._stack[-1]['dirty']

    def in_transaction(self):
        return bool(transaction_state._stack)

transaction_state = TransactionState()

def in_transaction():
    return transaction_state.in_transaction()

def queue_when_in_transaction(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if in_transaction():
            transaction_state.add_success_callback(func, args, kwargs)
        else:
            func(*args, **kwargs)
    return wrapper


def mark_transaction_dirty(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if in_transaction():
            transaction_state.mark_dirty()
        return func(*args, **kwargs)
    return wrapper


class AtomicMixIn(object):
    def __enter__(self):
        transaction_state.begin()
        self._no_monkey.__enter__(self)

    def __exit__(self, exc_type, exc_value, traceback):
        self._no_monkey.__exit__(self, exc_type, exc_value, traceback)
        connection = get_connection(self.using)
        if not connection.closed_in_transaction and exc_type is None and \
                not connection.needs_rollback:
            transaction_state.commit()
        else:
            transaction_state.rollback()


@once
def install_cacheops_transaction_support():
    monkey_mix(Atomic, AtomicMixIn)

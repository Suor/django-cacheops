# -*- coding: utf-8 -*-
import six
import threading

from funcy import wraps, once
from django.db.backends.utils import CursorWrapper
from django.db.transaction import get_connection, Atomic

from .utils import monkey_mix


__all__ = ('queue_when_in_transaction', 'install_cacheops_transaction_support',
           'transaction_state')


class TransactionState(threading.local):
    def __init__(self, *args, **kwargs):
        super(TransactionState, self).__init__(*args, **kwargs)
        self._stack = []

    def begin(self):
        self._stack.append({'cbs': [], 'dirty': False})

    def commit(self):
        context = self._stack.pop()
        if self._stack:
            # savepoint
            self._stack[-1]['cbs'].extend(context['cbs'])
            self._stack[-1]['dirty'] = self._stack[-1]['dirty'] or context['dirty']
        else:
            # transaction
            for func, args, kwargs in context['cbs']:
                func(*args, **kwargs)

    def rollback(self):
        self._stack.pop()

    def append(self, item):
        self._stack[-1]['cbs'].append(item)

    def in_transaction(self):
        return bool(self._stack)

    def mark_dirty(self):
        self._stack[-1]['dirty'] = True

    def is_dirty(self):
        return any(context['dirty'] for context in self._stack)

transaction_state = TransactionState()


def queue_when_in_transaction(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if transaction_state.in_transaction():
            transaction_state.append((func, args, kwargs))
        else:
            func(*args, **kwargs)
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


class CursorWrapperMixin(object):
    def callproc(self, procname, params=None):
        result = self._no_monkey.callproc(self, procname, params)
        if transaction_state.in_transaction():
            transaction_state.mark_dirty()
        return result

    def execute(self, sql, params=None):
        result = self._no_monkey.execute(self, sql, params)
        if transaction_state.in_transaction() and is_sql_dirty(sql):
            transaction_state.mark_dirty()
        return result

    def executemany(self, sql, param_list):
        result = self._no_monkey.executemany(self, sql, param_list)
        if transaction_state.in_transaction() and is_sql_dirty(sql):
            transaction_state.mark_dirty()
        return result


def is_sql_dirty(sql):
    # This should not happen as using bytes in Python 3 is against db protocol,
    # but some people will pass it anyway
    if six.PY3 and isinstance(sql, six.binary_type):
        sql = sql.decode()
    sql = sql.lower()
    return 'update' in sql or 'insert' in sql or 'delete' in sql


@once
def install_cacheops_transaction_support():
    monkey_mix(Atomic, AtomicMixIn)
    monkey_mix(CursorWrapper, CursorWrapperMixin)

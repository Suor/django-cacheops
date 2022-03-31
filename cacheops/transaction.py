import threading
from collections import defaultdict

from funcy import once, decorator

from django.db import DEFAULT_DB_ALIAS, DatabaseError
from django.db.backends.utils import CursorWrapper
from django.db.transaction import Atomic, get_connection, on_commit

from .utils import monkey_mix


__all__ = ('queue_when_in_transaction', 'install_cacheops_transaction_support',
           'transaction_states')


class TransactionState(list):
    def begin(self):
        self.append({'cbs': [], 'dirty': False})

    def commit(self):
        context = self.pop()
        if self:
            # savepoint
            self[-1]['cbs'].extend(context['cbs'])
            self[-1]['dirty'] = self[-1]['dirty'] or context['dirty']
        else:
            # transaction
            for func, args, kwargs in context['cbs']:
                func(*args, **kwargs)

    def rollback(self):
        self.pop()

    def push(self, item):
        self[-1]['cbs'].append(item)

    def mark_dirty(self):
        self[-1]['dirty'] = True

    def is_dirty(self):
        return any(context['dirty'] for context in self)

class TransactionStates(threading.local):
    def __init__(self):
        super(TransactionStates, self).__init__()
        self._states = defaultdict(TransactionState)

    def __getitem__(self, key):
        return self._states[key or DEFAULT_DB_ALIAS]

    def is_dirty(self, dbs):
        return any(self[db].is_dirty() for db in dbs)

transaction_states = TransactionStates()


@decorator
def queue_when_in_transaction(call):
    if transaction_states[call.using]:
        transaction_states[call.using].push((call, (), {}))
    else:
        return call()


class AtomicMixIn(object):
    def __enter__(self):
        entering = not transaction_states[self.using]
        transaction_states[self.using].begin()
        self._no_monkey.__enter__(self)
        if entering:
            on_commit(transaction_states[self.using].commit, self.using)

    def __exit__(self, exc_type, exc_value, traceback):
        connection = get_connection(self.using)
        try:
            self._no_monkey.__exit__(self, exc_type, exc_value, traceback)
        except DatabaseError:
            transaction_states[self.using].rollback()
            raise
        else:
            if not connection.closed_in_transaction and exc_type is None and \
                    not connection.needs_rollback:
                if transaction_states[self.using]:
                    transaction_states[self.using].commit()
            else:
                transaction_states[self.using].rollback()


class CursorWrapperMixin(object):
    def callproc(self, procname, params=None):
        result = self._no_monkey.callproc(self, procname, params)
        if transaction_states[self.db.alias]:
            transaction_states[self.db.alias].mark_dirty()
        return result

    def execute(self, sql, params=None):
        result = self._no_monkey.execute(self, sql, params)
        if transaction_states[self.db.alias] and is_sql_dirty(sql):
            transaction_states[self.db.alias].mark_dirty()
        return result

    def executemany(self, sql, param_list):
        result = self._no_monkey.executemany(self, sql, param_list)
        if transaction_states[self.db.alias] and is_sql_dirty(sql):
            transaction_states[self.db.alias].mark_dirty()
        return result


CHARS = set('abcdefghijklmnoprqstuvwxyz_')

def is_sql_dirty(sql):
    # This should not happen as using bytes in Python 3 is against db protocol,
    # but some people will pass it anyway
    if isinstance(sql, bytes):
        sql = sql.decode()
    # NOTE: not using regex here for speed
    sql = sql.lower()
    for action in ('update', 'insert', 'delete'):
        p = sql.find(action)
        if p == -1:
            continue
        start, end = p - 1, p + len(action)
        if (start < 0 or sql[start] not in CHARS) and (end >= len(sql) or sql[end] not in CHARS):
            return True
    else:
        return False


@once
def install_cacheops_transaction_support():
    monkey_mix(Atomic, AtomicMixIn)
    monkey_mix(CursorWrapper, CursorWrapperMixin)

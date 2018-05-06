from django.test import TestCase

from cacheops import invalidate_all
from cacheops.transaction import transaction_states


class BaseTestCase(TestCase):
    def setUp(self):
        # Emulate not being in transaction by tricking system to ignore its pretest level.
        # TestCase wraps each test into 1 or 2 transaction(s) altering cacheops behavior.
        # The alternative is using TransactionTestCase, which is 10x slow.
        from funcy import empty
        transaction_states._states, self._states \
            = empty(transaction_states._states), transaction_states._states

        invalidate_all()

    def tearDown(self):
        transaction_states._states = self._states


def make_inc(deco=lambda x: x):
    calls = [0]

    @deco
    def inc(_=None, **kw):
        calls[0] += 1
        return calls[0]

    inc.get = lambda: calls[0]
    return inc


# Thread utilities
import sys
from threading import Thread

from django.utils import six


class ThreadWithReturnValue(Thread):
    def __init__(self, *args, **kwargs):
        super(ThreadWithReturnValue, self).__init__(*args, **kwargs)
        self._return = None
        self._exc_info = None

    def run(self):
        try:
            if six.PY3:
                self._return = self._target(*self._args, **self._kwargs)
            else:
                self._return = self._Thread__target(*self._Thread__args, **self._Thread__kwargs)
        except Exception:
            self._exc_info = sys.exc_info()
        finally:
            # Django does not drop postgres connections opened in new threads.
            # This leads to postgres complaining about db accessed when we try to destory it.
            # See https://code.djangoproject.com/ticket/22420#comment:18
            from django.db import connection
            connection.close()

    def join(self, *args, **kwargs):
        super(ThreadWithReturnValue, self).join(*args, **kwargs)
        if self._exc_info:
            six.reraise(*self._exc_info)
        return self._return


def run_in_thread(target):
    t = ThreadWithReturnValue(target=target)
    t.start()
    return t.join()

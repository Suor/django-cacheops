import sys
import six
from threading import Thread


# Thread utilities

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


# A version of before_after that works with methods
# See https://github.com/c-oreills/before_after
#
# TODO: switch to normal dependency once it's fixed

from contextlib import contextmanager
from functools import wraps


def before(target, fn, **kwargs):
    return before_after(target, before_fn=fn, **kwargs)


def after(target, fn, **kwargs):
    return before_after(target, after_fn=fn, **kwargs)


@contextmanager
def before_after(
        target, before_fn=None, after_fn=None, once=True, **kwargs):
    def before_after_wrap(fn):
        called = []

        @wraps(fn)
        def inner(*a, **k):
            # If once is True, then don't call if this function has already
            # been called
            if once:
                if called:
                    return fn(*a, **k)
                else:
                    # Hack for lack of nonlocal keyword in Python 2: append to
                    # list to maked called truthy
                    called.append(True)

            if before_fn:
                before_fn(*a, **k)
            ret = fn(*a, **k)
            if after_fn:
                after_fn(*a, **k)
            return ret
        return inner

    from mock import patch

    patcher = patch(target, **kwargs)
    original, _ = patcher.get_original()
    patcher.new = before_after_wrap(original)
    with patcher:
        yield

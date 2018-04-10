import sys
from threading import Thread

from django.utils import six


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

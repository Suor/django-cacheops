# -*- coding: utf-8 -*-
from threading import Thread
import six

from django.db.transaction import atomic
from django.test import TransactionTestCase, override_settings

from .models import Category


class ThreadWithReturnValue(Thread):
    def __init__(self, *args, **kwargs):
        super(ThreadWithReturnValue, self).__init__(*args, **kwargs)
        self._return = None
        self._exc = None

    def run(self):
        try:
            if six.PY3:
                self._return = self._target(*self._args, **self._kwargs)
            else:
                self._return = self._Thread__target(*self._Thread__args, **self._Thread__kwargs)
        except Exception as e:
            self._exc = e
        finally:
            # Django does not drop postgres connections opened in new threads.
            # This leads to postgres complaining about db accessed when we try to destory it.
            # See https://code.djangoproject.com/ticket/22420#comment:18
            from django.db import connection
            connection.close()

    def join(self, *args, **kwargs):
        super(ThreadWithReturnValue, self).join(*args, **kwargs)
        if self._exc:
            raise self._exc
        return self._return


def run_in_thread(target):
    t = ThreadWithReturnValue(target=target)
    t.start()
    return t.join()


def get_category():
    return Category.objects.cache().get(pk=1)


class IntentionalRollback(Exception):
    pass


class TransactionSupportTests(TransactionTestCase):
    fixtures = ['basic']

    def test_atomic(self):
        with atomic():
            obj = get_category()
            obj.title = 'Changed'
            obj.save()
            self.assertEqual('Changed', get_category().title)
            self.assertEqual('Django', run_in_thread(get_category).title)
        self.assertEqual('Changed', run_in_thread(get_category).title)
        self.assertEqual('Changed', get_category().title)

    def test_nested(self):
        with atomic():
            with atomic():
                obj = get_category()
                obj.title = 'Changed'
                obj.save()
                self.assertEqual('Changed', get_category().title)
                self.assertEqual('Django', run_in_thread(get_category).title)
            self.assertEqual('Changed', get_category().title)
            self.assertEqual('Django', run_in_thread(get_category).title)
        self.assertEqual('Changed', run_in_thread(get_category).title)
        self.assertEqual('Changed', get_category().title)

    def test_rollback(self):
        try:
            with atomic():
                obj = get_category()
                obj.title = 'Changed'
                obj.save()
                self.assertEqual('Changed', get_category().title)
                self.assertEqual('Django', run_in_thread(get_category).title)
                raise IntentionalRollback()
        except IntentionalRollback:
            pass
        self.assertEqual('Django', get_category().title)
        self.assertEqual('Django', run_in_thread(get_category).title)

    def test_nested_rollback(self):
        with atomic():
            try:
                with atomic():
                    obj = get_category()
                    obj.title = 'Changed'
                    obj.save()
                    self.assertEqual('Changed', get_category().title)
                    self.assertEqual('Django', run_in_thread(get_category).title)
                    raise IntentionalRollback()
            except IntentionalRollback:
                pass
            self.assertEqual('Django', get_category().title)
            self.assertEqual('Django', run_in_thread(get_category).title)
        self.assertEqual('Django', get_category().title)
        self.assertEqual('Django', run_in_thread(get_category).title)

    @override_settings(CACHEOPS_TRANSACTION_SUPPORT=True)
    def test_transaction_support(self):
        with atomic():
            get_category()
            with self.assertNumQueries(0):
                get_category()
            with atomic():
                with self.assertNumQueries(0):
                    get_category()

            obj = get_category()
            obj.title += ' changed'
            obj.save()

            get_category()
            with self.assertNumQueries(1):
                get_category()

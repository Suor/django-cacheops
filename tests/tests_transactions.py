# -*- coding: utf-8 -*-
from threading import Thread

from django.db.transaction import atomic
from django.test import TransactionTestCase
from funcy import wraps
import six

from .models import Category


class ThreadWithReturnValue(Thread):
    def __init__(self, *args, **kwargs):
        super(ThreadWithReturnValue, self).__init__(*args, **kwargs)
        self._return = None

    def run(self):
        if six.PY3:
            if self._target is not None:
                self._return = self._target(*self._args, **self._kwargs)
        if six.PY2:
            if self._Thread__target is not None:
                self._return = self._Thread__target(*self._Thread__args, **self._Thread__kwargs)

    def join(self, *args, **kwargs):
        super(ThreadWithReturnValue, self).join(*args, **kwargs)
        return self._return


def return_from_other_thread(target, **kwargs):
    @wraps(target)
    def wrapper(*args, **wrapper_kwargs):
        try:
            return target(*args, **wrapper_kwargs)
        except Exception as e:
            # make sure other thread exceptions make it out to the test thread, instead of having
            # None returned and the test thread continuing.
            return e
        finally:
            # django does not drop postgres connections opened due to new threads. results in
            #  Postgres complaining about connected users when django tries to delete test db
            #  See https://code.djangoproject.com/ticket/22420#comment:18
            from django.db import connection
            connection.close()
    t = ThreadWithReturnValue(target=wrapper, **kwargs)
    t.start()
    results = t.join()
    # make sure other thread exceptions make it out to the test thread, instead of having
    # None returned and the test thread continuing.
    if isinstance(results, Exception):
        raise results
    return results


def get_category_pk1_title():
    return Category.objects.cache().get(pk=1).title


class IntentionalRollback(Exception):
    pass


class TransactionalInvalidationTests(TransactionTestCase):
    fixtures = ['basic']

    def test_atomic_block_change(self):
        with atomic():
            obj = Category.objects.cache().get(pk=1)
            obj.title = 'Changed'
            obj.save()
            self.assertEqual('Changed', get_category_pk1_title())
            self.assertEqual('Django', return_from_other_thread(get_category_pk1_title))
        self.assertEqual('Changed', return_from_other_thread(get_category_pk1_title))
        self.assertEqual('Changed', get_category_pk1_title())

    def test_nested_atomic_block_change(self):
        with atomic():
            with atomic():
                obj = Category.objects.cache().get(pk=1)
                obj.title = 'Changed'
                obj.save()
                self.assertEqual('Changed', get_category_pk1_title())
                self.assertEqual('Django', return_from_other_thread(get_category_pk1_title))
            self.assertEqual('Changed', get_category_pk1_title())
            self.assertEqual('Django', return_from_other_thread(get_category_pk1_title))
        self.assertEqual('Changed', return_from_other_thread(get_category_pk1_title))
        self.assertEqual('Changed', get_category_pk1_title())

    def test_atomic_block_change_with_rollback(self):
        try:
            with atomic():
                obj = Category.objects.cache().get(pk=1)
                obj.title = 'Changed'
                obj.save()
                self.assertEqual('Changed', get_category_pk1_title())
                self.assertEqual('Django', return_from_other_thread(get_category_pk1_title))
                raise IntentionalRollback()
        except IntentionalRollback:
            pass
        self.assertEqual('Django', get_category_pk1_title())
        self.assertEqual('Django', return_from_other_thread(get_category_pk1_title))

    def test_nested_atomic_block_change_with_rollback(self):
        with atomic():
            try:
                with atomic():
                    obj = Category.objects.cache().get(pk=1)
                    obj.title = 'Changed'
                    obj.save()
                    self.assertEqual('Changed', get_category_pk1_title())
                    self.assertEqual('Django', return_from_other_thread(get_category_pk1_title))
                    raise IntentionalRollback()
            except IntentionalRollback:
                pass
            self.assertEqual('Django', get_category_pk1_title())
            self.assertEqual('Django', return_from_other_thread(get_category_pk1_title))
        self.assertEqual('Django', get_category_pk1_title())
        self.assertEqual('Django', return_from_other_thread(get_category_pk1_title))

from django.db import connection, IntegrityError
from django.db.transaction import atomic
from django.test import TransactionTestCase

from cacheops.transaction import is_sql_dirty, queue_when_in_transaction

from .models import Category, Post
from .utils import run_in_thread


def get_category():
    return Category.objects.cache().get(pk=1)


class IntentionalRollback(Exception):
    pass


class TransactionSupportTests(TransactionTestCase):
    databases = ('default', 'slave')
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

    def test_smart_transactions(self):
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

    def test_rollback_during_integrity_error(self):
        # store category in cache
        get_category()

        # Make current DB be "dirty" by write
        with self.assertRaises(IntegrityError):
            with atomic():
                Post.objects.create(category_id=-1, title='')

        # however, this write should be rolled back and current DB should
        # not be "dirty"

        with self.assertNumQueries(0):
            get_category()

    def test_call_cacheops_cbs_before_on_commit_cbs(self):
        calls = []

        with atomic():
            def django_commit_handler():
                calls.append('django')
            connection.on_commit(django_commit_handler)

            @queue_when_in_transaction
            def cacheops_commit_handler(using):
                calls.append('cacheops')
            cacheops_commit_handler('default')

        self.assertEqual(calls, ['cacheops', 'django'])

    def test_is_sql_dirty_with_non_string_sql(self):
        """Non-string SQL objects (e.g. psycopg sql.Composed) should not crash is_sql_dirty (#377)."""

        class FakeComposed:
            """Mimics psycopg2/psycopg3 sql.Composed which has as_string() but no lower()."""
            def __init__(self, text):
                self._text = text
            def as_string(self, context):
                return self._text

        self.assertTrue(is_sql_dirty(FakeComposed("DELETE FROM some_table")))
        self.assertTrue(is_sql_dirty(FakeComposed("INSERT INTO foo VALUES (1)")))
        self.assertTrue(is_sql_dirty(FakeComposed("UPDATE foo SET bar = 1")))
        self.assertFalse(is_sql_dirty(FakeComposed("SELECT * FROM foo")))

    def test_multidb(self):
        try:
            with atomic('slave'):
                with atomic():
                    obj = get_category()
                    obj.title = 'Changed'
                    obj.save()
                raise IntentionalRollback()
        except IntentionalRollback:
            pass
        self.assertEqual('Changed', get_category().title)

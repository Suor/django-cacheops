from django.db import transaction
from django.test import TestCase, override_settings

from cacheops import cached_as, no_invalidation, invalidate_obj, invalidate_model, invalidate_all
from cacheops.conf import settings
from cacheops.signals import cache_read, cache_invalidated

from .utils import BaseTestCase, make_inc
from .models import Post, Category, Local, DbAgnostic, DbBinded


class SettingsTests(TestCase):
    def test_context_manager(self):
        self.assertTrue(settings.CACHEOPS_ENABLED)

        with self.settings(CACHEOPS_ENABLED=False):
            self.assertFalse(settings.CACHEOPS_ENABLED)

    @override_settings(CACHEOPS_ENABLED=False)
    def test_decorator(self):
        self.assertFalse(settings.CACHEOPS_ENABLED)


@override_settings(CACHEOPS_ENABLED=False)
class ClassOverrideSettingsTests(TestCase):
    def test_class(self):
        self.assertFalse(settings.CACHEOPS_ENABLED)


class SignalsTests(BaseTestCase):
    def setUp(self):
        super(SignalsTests, self).setUp()

        def set_signal(signal=None, **kwargs):
            self.signal_calls.append(kwargs)

        self.signal_calls = []
        cache_read.connect(set_signal, dispatch_uid=1, weak=False)

    def tearDown(self):
        super(SignalsTests, self).tearDown()
        cache_read.disconnect(dispatch_uid=1)

    def test_queryset(self):
        # Miss
        test_model = Category.objects.create(title="foo")
        Category.objects.cache().get(id=test_model.id)
        self.assertEqual(self.signal_calls, [{'sender': Category, 'func': None, 'hit': False}])

        # Hit
        self.signal_calls = []
        Category.objects.cache().get(id=test_model.id) # hit
        self.assertEqual(self.signal_calls, [{'sender': Category, 'func': None, 'hit': True}])

    def test_queryset_empty(self):
        list(Category.objects.cache().filter(pk__in=[]))
        self.assertEqual(self.signal_calls, [{'sender': Category, 'func': None, 'hit': False}])

    def test_cached_as(self):
        get_calls = make_inc(cached_as(Category.objects.filter(title='test')))
        func = get_calls.__wrapped__

        # Miss
        self.assertEqual(get_calls(), 1)
        self.assertEqual(self.signal_calls, [{'sender': None, 'func': func, 'hit': False}])

        # Hit
        self.signal_calls = []
        self.assertEqual(get_calls(), 1)
        self.assertEqual(self.signal_calls, [{'sender': None, 'func': func, 'hit': True}])

    def test_invalidation_signal(self):
        def set_signal(signal=None, **kwargs):
            signal_calls.append(kwargs)

        signal_calls = []
        cache_invalidated.connect(set_signal, dispatch_uid=1, weak=False)

        invalidate_all()
        invalidate_model(Post)
        c = Category.objects.create(title='Hey')
        self.assertEqual(signal_calls, [
            {'sender': None, 'obj_dict': None},
            {'sender': Post, 'obj_dict': None},
            {'sender': Category, 'obj_dict': {'id': c.pk, 'title': 'Hey'}},
        ])


class LockingTests(BaseTestCase):
    def test_lock(self):
        import random
        import threading
        from .utils import ThreadWithReturnValue
        from before_after import before

        @cached_as(Post, lock=True, timeout=60)
        def func():
            return random.random()

        results = []
        locked = threading.Event()
        thread = [None]

        def second_thread():
            def _target():
                try:
                    with before('redis.Redis.brpoplpush', lambda *a, **kw: locked.set()):
                        results.append(func())
                except Exception:
                    locked.set()
                    raise

            thread[0] = ThreadWithReturnValue(target=_target)
            thread[0].start()
            assert locked.wait(1)  # Wait until right before the block

        with before('random.random', second_thread):
            results.append(func())

        thread[0].join()

        self.assertEqual(results[0], results[1])


class NoInvalidationTests(BaseTestCase):
    fixtures = ['basic']

    def _template(self, invalidate):
        post = Post.objects.cache().get(pk=1)
        invalidate(post)

        with self.assertNumQueries(0):
            Post.objects.cache().get(pk=1)

    def test_context_manager(self):
        def invalidate(post):
            with no_invalidation:
                invalidate_obj(post)
        self._template(invalidate)

    def test_decorator(self):
        self._template(no_invalidation(invalidate_obj))

    def test_nested(self):
        def invalidate(post):
            with no_invalidation:
                with no_invalidation:
                    pass
                invalidate_obj(post)
        self._template(invalidate)

    def test_in_transaction(self):
        with transaction.atomic():
            post = Post.objects.cache().get(pk=1)

            with no_invalidation:
                post.save()

        with self.assertNumQueries(0):
            Post.objects.cache().get(pk=1)


class LocalGetTests(BaseTestCase):
    def setUp(self):
        Local.objects.create(pk=1)
        super(LocalGetTests, self).setUp()

    def test_unhashable_args(self):
        Local.objects.cache().get(pk__in=[1, 2])


class DbAgnosticTests(BaseTestCase):
    databases = ('default', 'slave')

    def test_db_agnostic_by_default(self):
        list(DbAgnostic.objects.cache())

        with self.assertNumQueries(0, using='slave'):
            list(DbAgnostic.objects.cache().using('slave'))

    def test_db_agnostic_disabled(self):
        list(DbBinded.objects.cache())

        with self.assertNumQueries(1, using='slave'):
            list(DbBinded.objects.cache().using('slave'))


def test_model_family():
    from cacheops.utils import model_family
    from .models import Abs, Concrete1, AbsChild, Concrete2
    from .models import NoProfile, NoProfileProxy, AbsNoProfile, NoProfileChild
    from .models import ParentId, ParentStr, Mess, MessChild

    # Abstract models do not have family, children of an abstract model are not a family
    assert model_family(Abs) == set()
    assert model_family(Concrete1) == {Concrete1}
    assert model_family(AbsChild) == set()
    assert model_family(Concrete2) == {Concrete2}

    # Everything in but an abstract model
    assert model_family(NoProfile) == {NoProfile, NoProfileProxy, NoProfileChild}
    assert model_family(NoProfileProxy) == {NoProfile, NoProfileProxy, NoProfileChild}
    assert model_family(AbsNoProfile) == set()
    assert model_family(NoProfileChild) == {NoProfile, NoProfileProxy, NoProfileChild}

    # The worst of multiple inheritance
    assert model_family(Mess) == {Mess, MessChild, ParentId, ParentStr}

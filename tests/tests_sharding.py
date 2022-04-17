from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from cacheops import cache, CacheMiss
from .models import Category, Post, Extra
from .utils import BaseTestCase


class PrefixTests(BaseTestCase):
    databases = ('default', 'slave')
    fixtures = ['basic']

    def test_context(self):
        prefix = ['']
        with override_settings(CACHEOPS_PREFIX=lambda _: prefix[0]):
            with self.assertNumQueries(2):
                Category.objects.cache().count()
                prefix[0] = 'x'
                Category.objects.cache().count()

    @override_settings(CACHEOPS_PREFIX=lambda q: q.db)
    def test_db(self):
        with self.assertNumQueries(1):
            list(Category.objects.cache())

        with self.assertNumQueries(1, using='slave'):
            list(Category.objects.cache().using('slave'))
            list(Category.objects.cache().using('slave'))

    @override_settings(CACHEOPS_PREFIX=lambda q: q.table)
    def test_table(self):
        self.assertTrue(Category.objects.all()._cache_key().startswith('tests_category'))

        with self.assertRaises(ImproperlyConfigured):
            list(Post.objects.filter(category__title='Django').cache())

    @override_settings(CACHEOPS_PREFIX=lambda q: q.table)
    def test_self_join_tables(self):
        list(Extra.objects.filter(to_tag__pk=1).cache())

    @override_settings(CACHEOPS_PREFIX=lambda q: q.table)
    def test_union_tables(self):
        qs = Post.objects.filter(pk=1).union(Post.objects.filter(pk=2)).cache()
        list(qs)


class SimpleCacheTests(BaseTestCase):
    def test_prefix(self):
        with override_settings(CACHEOPS_PREFIX=lambda _: 'a'):
            cache.set("key", "value")
            self.assertEqual(cache.get("key"), "value")

        with self.assertRaises(CacheMiss):
            cache.get("key")

from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from tests.models import Category, Post, Extra
from tests.utils import BaseTestCase, gen_cluster_prefix


class PrefixTests(BaseTestCase):
    databases = ('default', 'slave')
    fixtures = ['basic']

    def test_context(self):
        prefix = ['test_prefix']
        with override_settings(CACHEOPS_PREFIX=lambda _: gen_cluster_prefix(prefix[0])):
            with self.assertNumQueries(2):
                Category.objects.cache().count()
                prefix[0] = gen_cluster_prefix('x')
                Category.objects.cache().count()

    @override_settings(CACHEOPS_PREFIX=lambda q: gen_cluster_prefix(q.db))
    def test_db(self):
        with self.assertNumQueries(1):
            list(Category.objects.cache())

        with self.assertNumQueries(1, using='slave'):
            list(Category.objects.cache().using('slave'))
            list(Category.objects.cache().using('slave'))

    @override_settings(CACHEOPS_PREFIX=lambda q: gen_cluster_prefix(q.table))
    def test_table(self):
        self.assertTrue(Category.objects.all()._cache_key().startswith('{tests_category}'))

        with self.assertRaises(ImproperlyConfigured):
            list(Post.objects.filter(category__title='Django').cache())

    @override_settings(CACHEOPS_PREFIX=lambda q: gen_cluster_prefix(q.table))
    def test_self_join_tables(self):
        list(Extra.objects.filter(to_tag__pk=1).cache())

    @override_settings(CACHEOPS_PREFIX=lambda q: gen_cluster_prefix(q.table))
    def test_union_tables(self):
        qs = Post.objects.filter(pk=1).union(Post.objects.filter(pk=2)).cache()
        list(qs)

# -*- coding: utf-8 -*-
import re
import unittest

import django
from django.db import connection, connections
from django.test import TestCase
from django.test.client import RequestFactory
from django.contrib.auth.models import User
from django.template import Context, Template
from django.db.models import F, Count

from cacheops import invalidate_all, invalidate_model, invalidate_obj, no_invalidation, \
                     cached, cached_view, cached_as, cached_view_as
from cacheops import invalidate_fragment
from cacheops.templatetags.cacheops import register
from cacheops.transaction import transaction_states
from cacheops.signals import cache_read, cache_invalidated

decorator_tag = register.decorator_tag
from .models import *  # noqa


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


class BasicTests(BaseTestCase):
    fixtures = ['basic']

    def test_it_works(self):
        with self.assertNumQueries(1):
            cnt1 = Category.objects.cache().count()
            cnt2 = Category.objects.cache().count()
            self.assertEqual(cnt1, cnt2)

    def test_empty(self):
        with self.assertNumQueries(0):
            list(Category.objects.cache().filter(id__in=[]))

    def test_exact(self):
        list(Category.objects.filter(pk=1).cache())
        with self.assertNumQueries(0):
            list(Category.objects.filter(pk__exact=1).cache())

    def test_exists(self):
        with self.assertNumQueries(1):
            Category.objects.cache(ops='exists').exists()
            Category.objects.cache(ops='exists').exists()

    def test_some(self):
        # Ignoring SOME condition lead to wrong DNF for this queryset,
        # which leads to no invalidation
        list(Category.objects.exclude(pk__in=range(10), pk__isnull=False).cache())
        c = Category.objects.get(pk=1)
        c.save()
        with self.assertNumQueries(1):
            list(Category.objects.exclude(pk__in=range(10), pk__isnull=False).cache())

    def test_invalidation(self):
        post = Post.objects.cache().get(pk=1)
        post.title += ' changed'
        post.save()

        with self.assertNumQueries(1):
            changed_post = Post.objects.cache().get(pk=1)
            self.assertEqual(post.title, changed_post.title)

    def test_granular(self):
        Post.objects.cache().get(pk=1)
        Post.objects.get(pk=2).save()

        with self.assertNumQueries(0):
            Post.objects.cache().get(pk=1)

    def test_invalidate_by_foreign_key(self):
        posts = list(Post.objects.cache().filter(category=1))
        Post.objects.create(title='New Post', category_id=1)

        with self.assertNumQueries(1):
            changed_posts = list(Post.objects.cache().filter(category=1))
            self.assertEqual(len(changed_posts), len(posts) + 1)

    def test_invalidate_by_one_to_one(self):
        extras = list(Extra.objects.cache().filter(post=3))
        Extra.objects.create(post_id=3, tag=0)

        with self.assertNumQueries(1):
            changed_extras = list(Extra.objects.cache().filter(post=3))
            self.assertEqual(len(changed_extras), len(extras) + 1)

    def test_invalidate_by_boolean(self):
        count = Post.objects.cache().filter(visible=True).count()

        post = Post.objects.get(pk=1, visible=True)
        post.visible = False
        post.save()

        with self.assertNumQueries(1):
            new_count = Post.objects.cache().filter(visible=True).count()
            self.assertEqual(new_count, count - 1)

    def test_bulk_create(self):
        cnt = Category.objects.cache().count()
        Category.objects.bulk_create([Category(title='hi'), Category(title='there')])

        with self.assertNumQueries(1):
            cnt2 = Category.objects.cache().count()
            self.assertEqual(cnt2, cnt + 2)

    def test_db_column(self):
        e = Extra.objects.cache().get(tag=5)
        e.save()

    def test_fk_to_db_column(self):
        e = Extra.objects.cache().get(to_tag__tag=5)
        e.save()

        with self.assertNumQueries(1):
            Extra.objects.cache().get(to_tag=5)

    def test_expressions(self):
        qs = Extra.objects.cache().filter(tag=F('to_tag') + 1, to_tag=F('tag').bitor(5))
        qs.count()
        with self.assertNumQueries(0):
            qs.count()

    def test_expressions_save(self):
        # Check saving F
        extra = Extra.objects.get(pk=1)
        extra.tag = F('tag')
        extra.save()

        # Check saving ExressionNode
        Extra.objects.create(post_id=3, tag=7)
        extra = Extra.objects.get(pk=3)
        extra.tag = F('tag') + 1
        extra.save()

    def test_combine(self):
        qs = Post.objects.filter(pk__in=[1, 2]) & Post.objects.all()
        self.assertEqual(list(qs.cache()), list(qs))

        qs = Post.objects.filter(pk__in=[1, 2]) | Post.objects.none()
        self.assertEqual(list(qs.cache()), list(qs))

    def test_first_and_last(self):
        qs = Category.objects.cache(ops='get')
        qs.first()
        qs.last()
        with self.assertNumQueries(0):
            qs.first()
            qs.last()

    @unittest.skipIf(django.VERSION < (1, 11), 'Union added in Django 1.11')
    def test_union(self):
        qs = Post.objects.filter(category=1).values('id', 'title').union(
                Category.objects.filter(title='Perl').values('id', 'title')).cache()
        list(qs.clone())
        # Invalidated
        Category.objects.create(title='Perl')
        with self.assertNumQueries(1):
            list(qs.clone())
        # Not invalidated
        Category.objects.create(title='Ruby')
        with self.assertNumQueries(0):
            list(qs.clone())

    def test_invalidated_update(self):
        list(Post.objects.filter(category=1).cache())
        list(Post.objects.filter(category=2).cache())

        # Should invalidate both queries
        Post.objects.filter(category=1).invalidated_update(category=2)

        with self.assertNumQueries(2):
            list(Post.objects.filter(category=1).cache())
            list(Post.objects.filter(category=2).cache())


class ValuesTests(BaseTestCase):
    fixtures = ['basic']

    def test_it_works(self):
        with self.assertNumQueries(1):
            len(Category.objects.cache().values())
            len(Category.objects.cache().values())

    def test_it_varies_on_class(self):
        with self.assertNumQueries(2):
            len(Category.objects.cache())
            len(Category.objects.cache().values())

    def test_it_varies_on_flat(self):
        with self.assertNumQueries(2):
            len(Category.objects.cache().values_list())
            len(Category.objects.cache().values_list(flat=True))


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


class DecoratorTests(BaseTestCase):
    def test_cached_as_model(self):
        get_calls = _make_inc(cached_as(Category))

        self.assertEqual(get_calls(), 1)      # miss
        self.assertEqual(get_calls(), 1)      # hit
        Category.objects.create(title='test') # invalidate
        self.assertEqual(get_calls(), 2)      # miss

    def test_cached_as_cond(self):
        get_calls = _make_inc(cached_as(Category.objects.filter(title='test')))

        self.assertEqual(get_calls(), 1)      # cache
        Category.objects.create(title='miss') # don't invalidate
        self.assertEqual(get_calls(), 1)      # hit
        Category.objects.create(title='test') # invalidate
        self.assertEqual(get_calls(), 2)      # miss

    def test_cached_as_obj(self):
        c = Category.objects.create(title='test')
        get_calls = _make_inc(cached_as(c))

        self.assertEqual(get_calls(), 1)      # cache
        Category.objects.create(title='miss') # don't invalidate
        self.assertEqual(get_calls(), 1)      # hit
        c.title = 'new'
        c.save()                              # invalidate
        self.assertEqual(get_calls(), 2)      # miss

    def test_cached_as_depends_on_args(self):
        get_calls = _make_inc(cached_as(Category))

        self.assertEqual(get_calls(1), 1)      # cache
        self.assertEqual(get_calls(1), 1)      # hit
        self.assertEqual(get_calls(2), 2)      # miss

    def test_cached_as_depends_on_two_models(self):
        get_calls = _make_inc(cached_as(Category, Post))
        c = Category.objects.create(title='miss')
        p = Post.objects.create(title='New Post', category=c)

        self.assertEqual(get_calls(1), 1)      # cache
        c.title = 'new title'
        c.save()                               # invalidate by Category
        self.assertEqual(get_calls(1), 2)      # miss and cache
        p.title = 'new title'
        p.save()                               # invalidate by Post
        self.assertEqual(get_calls(1), 3)      # miss and cache

    def test_cached_view_as(self):
        get_calls = _make_inc(cached_view_as(Category))

        factory = RequestFactory()
        r1 = factory.get('/hi')
        r2 = factory.get('/hi')
        r2.META['REMOTE_ADDR'] = '10.10.10.10'
        r3 = factory.get('/bye')

        self.assertEqual(get_calls(r1), 1) # cache
        self.assertEqual(get_calls(r1), 1) # hit
        self.assertEqual(get_calls(r2), 1) # hit, since only url is considered
        self.assertEqual(get_calls(r3), 2) # miss

    def test_cached_view_on_template_response(self):
        from django.template.response import TemplateResponse
        from django.template import engines
        from_string = engines['django'].from_string

        @cached_view_as(Category)
        def view(request):
            return TemplateResponse(request, from_string('hi'))

        factory = RequestFactory()
        view(factory.get('/hi'))


from datetime import date, datetime, time

class WeirdTests(BaseTestCase):
    def _template(self, field, value):
        qs = Weird.objects.cache().filter(**{field: value})
        count = qs.count()

        obj = Weird.objects.create(**{field: value})

        with self.assertNumQueries(2):
            self.assertEqual(qs.count(), count + 1)
            new_obj = qs.get(pk=obj.pk)
            self.assertEqual(getattr(new_obj, field), value)

    def test_date(self):
        self._template('date_field', date.today())

    def test_datetime(self):
        # NOTE: some databases (mysql) don't store microseconds
        self._template('datetime_field', datetime.now().replace(microsecond=0))

    def test_time(self):
        self._template('time_field', time(10, 30))

    def test_list(self):
        self._template('list_field', [1, 2])

    def test_binary(self):
        obj = Weird.objects.create(binary_field=b'12345')
        Weird.objects.cache().get(pk=obj.pk)
        Weird.objects.cache().get(pk=obj.pk)

    def test_custom(self):
        self._template('custom_field', CustomValue('some'))

    def test_weird_custom(self):
        class WeirdCustom(CustomValue):
            def __str__(self):
                return 'other'
        self._template('custom_field', WeirdCustom('some'))

    def test_custom_query(self):
        list(Weird.customs.cache())


@unittest.skipIf(connection.vendor != 'postgresql', "Only for PostgreSQL")
class PostgresTests(BaseTestCase):
    def test_array_contains(self):
        list(TaggedPost.objects.filter(tags__contains=[42]).cache())

    def test_array_len(self):
        list(TaggedPost.objects.filter(tags__len=42).cache())

    @unittest.skipIf(django.VERSION < (1, 9), "JSONField added in Django 1.9")
    def test_json(self):
        list(TaggedPost.objects.filter(meta__author='Suor'))


class TemplateTests(BaseTestCase):
    def assertRendersTo(self, template, context, result):
        s = template.render(Context(context))
        self.assertEqual(re.sub(r'\s+', '', s), result)

    def test_cached(self):
        inc_a = _make_inc()
        inc_b = _make_inc()
        t = Template("""
            {% load cacheops %}
            {% cached 60 'a' %}.a{{ a }}{% endcached %}
            {% cached 60 'a' %}.a{{ a }}{% endcached %}
            {% cached 60 'a' 'variant' %}.a{{ a }}{% endcached %}
            {% cached timeout=60 fragment_name='b' %}.b{{ b }}{% endcached %}
        """)

        self.assertRendersTo(t, {'a': inc_a, 'b': inc_b}, '.a1.a1.a2.b1')

    def test_invalidate_fragment(self):
        inc = _make_inc()
        t = Template("""
            {% load cacheops %}
            {% cached 60 'a' %}.{{ inc }}{% endcached %}
        """)

        self.assertRendersTo(t, {'inc': inc}, '.1')

        invalidate_fragment('a')
        self.assertRendersTo(t, {'inc': inc}, '.2')

    def test_cached_as(self):
        inc = _make_inc()
        qs = Post.objects.all()
        t = Template("""
            {% load cacheops %}
            {% cached_as qs None 'a' %}.{{ inc }}{% endcached_as %}
            {% cached_as qs timeout=60 fragment_name='a' %}.{{ inc }}{% endcached_as %}
            {% cached_as qs fragment_name='a' timeout=60 %}.{{ inc }}{% endcached_as %}
        """)

        # All the forms are equivalent
        self.assertRendersTo(t, {'inc': inc, 'qs': qs}, '.1.1.1')

        # Cache works across calls
        self.assertRendersTo(t, {'inc': inc, 'qs': qs}, '.1.1.1')

        # Post invalidation clears cache
        invalidate_model(Post)
        self.assertRendersTo(t, {'inc': inc, 'qs': qs}, '.2.2.2')

    def test_decorator_tag(self):
        @decorator_tag
        def my_cached(flag):
            return cached(timeout=60) if flag else lambda x: x

        inc = _make_inc()
        t = Template("""
            {% load cacheops %}
            {% my_cached 1 %}.{{ inc }}{% endmy_cached %}
            {% my_cached 0 %}.{{ inc }}{% endmy_cached %}
            {% my_cached 0 %}.{{ inc }}{% endmy_cached %}
            {% my_cached 1 %}.{{ inc }}{% endmy_cached %}
        """)

        self.assertRendersTo(t, {'inc': inc}, '.1.2.3.1')

    def test_decorator_tag_context(self):
        @decorator_tag(takes_context=True)
        def my_cached(context):
            return cached(timeout=60) if context['flag'] else lambda x: x

        inc = _make_inc()
        t = Template("""
            {% load cacheops %}
            {% my_cached %}.{{ inc }}{% endmy_cached %}
            {% my_cached %}.{{ inc }}{% endmy_cached %}
        """)

        self.assertRendersTo(t, {'inc': inc, 'flag': True}, '.1.1')
        self.assertRendersTo(t, {'inc': inc, 'flag': False}, '.2.3')


class IssueTests(BaseTestCase):
    fixtures = ['basic']

    def setUp(self):
        self.user = User.objects.create(pk=1, username='Suor')
        Profile.objects.create(pk=2, user=self.user, tag=10)
        super(IssueTests, self).setUp()

    def test_16(self):
        p = Profile.objects.cache().get(user__id__exact=1)
        p.save()

        with self.assertNumQueries(1):
            Profile.objects.cache().get(user=1)

    def test_29(self):
        Brand.objects.exclude(labels__in=[1, 2, 3]).cache().count()

    def test_45(self):
        m = CacheOnSaveModel(title="test")
        m.save()

        with self.assertNumQueries(0):
            CacheOnSaveModel.objects.cache().get(pk=m.pk)

    def test_57(self):
        list(Post.objects.filter(category__in=Category.objects.nocache()).cache())

    def test_114(self):
        list(Category.objects.cache().filter(title=u'ó'))

    def test_145(self):
        # Create One with boolean False
        one = One.objects.create(boolean=False)

        # Update boolean to True
        one = One.objects.cache().get(id=one.id)
        one.boolean = True
        one.save()  # An error was in post_save signal handler

    def test_159(self):
        brand = Brand.objects.create(pk=1)
        label = Label.objects.create(pk=2)
        brand.labels.add(label)

        # Create another brand with the same pk as label.
        # This will trigger a bug invalidating brands quering them by label id.
        another_brand = Brand.objects.create(pk=2)

        list(brand.labels.cache())
        list(another_brand.labels.cache())

        # Clear brands for label linked to brand, but not another_brand.
        label.brands.clear()

        # Cache must stay for another_brand
        with self.assertNumQueries(0):
            list(another_brand.labels.cache())

    def test_161(self):
        categories = Category.objects.using('slave').filter(title='Python')
        list(Post.objects.using('slave').filter(category__in=categories).cache())

    @unittest.skipIf(connection.vendor == 'mysql', 'MySQL fails with encodings')
    def test_161_non_ascii(self):
        # Non ascii text in non-unicode str literal
        list(Category.objects.filter(title='фыва').cache())
        list(Category.objects.filter(title='фыва', title__startswith='фыва').cache())

    def test_169(self):
        c = Category.objects.prefetch_related('posts').get(pk=3)
        c.posts.get(visible=1)  # this used to fail

    def test_173(self):
        extra = Extra.objects.get(pk=1)
        title = extra.post.category.title

        # Cache
        list(Extra.objects.filter(post__category__title=title).cache())

        # Break the link
        extra.post.category_id = 2
        extra.post.save()

        # Fail because neither Extra nor Catehory changed, but something in between
        self.assertEqual([], list(Extra.objects.filter(post__category__title=title).cache()))

    def test_177(self):
        c = Category.objects.get(pk=1)
        c.posts_copy = c.posts.cache()
        bool(c.posts_copy)

    def test_217(self):
        # Destroy and recreate model manager
        Post.objects.__class__().contribute_to_class(Post, 'objects')

        # Test invalidation
        post = Post.objects.cache().get(pk=1)
        post.title += ' changed'
        post.save()

        with self.assertNumQueries(1):
            changed_post = Post.objects.cache().get(pk=1)
            self.assertEqual(post.title, changed_post.title)

    def test_232(self):
        list(Post.objects.cache().filter(category__in=[None, 1]).filter(category=1))

    @unittest.skipIf(connection.vendor == 'mysql', 'In MySQL DDL is not transaction safe')
    def test_265(self):
        # Databases must have different structure,
        # so exception other then DoesNotExist would be raised.
        # Let's delete tests_video from default database
        # and try working with it in slave database with using.
        # Table is not restored automatically in MySQL, so I disabled this test in MySQL.
        connection.cursor().execute("DROP TABLE tests_video;")

        # Works fine
        c = Video.objects.db_manager('slave').create(title='test_265')
        self.assertTrue(Video.objects.using('slave').filter(title='test_265').exists())

        # Fails with "no such table: tests_video"
        # Fixed by adding .using(instance._state.db) in query.ManagerMixin._pre_save() method
        c.title = 'test_265_1'
        c.save()
        self.assertTrue(Video.objects.using('slave').filter(title='test_265_1').exists())

        # This also didn't work before fix above. Test that it works.
        c.title = 'test_265_2'
        c.save(using='slave')
        self.assertTrue(Video.objects.using('slave').filter(title='test_265_2').exists())

        # Same bug in other method
        # Fixed by adding .using(self._db) in query.QuerySetMixin.invalidated_update() method
        Video.objects.using('slave').invalidated_update(title='test_265_3')
        self.assertTrue(Video.objects.using('slave').filter(title='test_265_3').exists())


class LocalGetTests(BaseTestCase):
    def setUp(self):
        Local.objects.create(pk=1)
        super(LocalGetTests, self).setUp()

    def test_unhashable_args(self):
        Local.objects.cache().get(pk__in=[1, 2])


class RelatedTests(BaseTestCase):
    fixtures = ['basic']

    def _template(self, qs, change, should_invalidate=True):
        list(qs._clone().cache())
        change()
        with self.assertNumQueries(1 if should_invalidate else 0):
            list(qs.cache())

    def test_related_invalidation(self):
        self._template(
            Post.objects.filter(category__title='Django'),
            lambda: Category.objects.get(title='Django').save()
        )

    def test_reverse_fk(self):
        self._template(
            Category.objects.filter(posts__title='Cacheops'),
            lambda: Post.objects.get(title='Cacheops').save()
        )

    def test_reverse_fk_same(self):
        title = "Implicit variable as pronoun"
        self._template(
            Category.objects.filter(posts__title=title, posts__visible=True),
            lambda: Post.objects.get(title=title, visible=True).save()
        )
        self._template(
            Category.objects.filter(posts__title=title, posts__visible=False),
            lambda: Post.objects.get(title=title, visible=True).save(),
            should_invalidate=False,
        )

    def test_reverse_fk_separate(self):
        title = "Implicit variable as pronoun"
        self._template(
            Category.objects.filter(posts__title=title).filter(posts__visible=True),
            lambda: Post.objects.get(title=title, visible=True).save()
        )
        self._template(
            Category.objects.filter(posts__title=title).filter(posts__visible=False),
            lambda: Post.objects.get(title=title, visible=True).save(),
        )


class AggregationTests(BaseTestCase):
    fixtures = ['basic']

    def test_annotate(self):
        qs = Category.objects.annotate(posts_count=Count('posts')).cache()
        list(qs._clone())
        Post.objects.create(title='New One', category=Category.objects.all()[0])
        with self.assertNumQueries(1):
            list(qs._clone())

    def test_aggregate(self):
        qs = Category.objects.cache()
        qs.aggregate(posts_count=Count('posts'))
        # Test caching
        with self.assertNumQueries(0):
            qs.aggregate(posts_count=Count('posts'))
        # Test invalidation
        Post.objects.create(title='New One', category=Category.objects.all()[0])
        with self.assertNumQueries(1):
            qs.aggregate(posts_count=Count('posts'))


class M2MTests(BaseTestCase):
    brand_cls = Brand
    label_cls = Label

    def setUp(self):
        self.bf = self.brand_cls.objects.create()
        self.bs = self.brand_cls.objects.create()

        self.fast = self.label_cls.objects.create(text='fast')
        self.slow = self.label_cls.objects.create(text='slow')
        self.furious = self.label_cls.objects.create(text='furios')

        self.setup_m2m()
        super(M2MTests, self).setUp()

    def setup_m2m(self):
        self.bf.labels.add(self.fast, self.furious)
        self.bs.labels.add(self.slow, self.furious)

    def _template(self, qs_or_action, change, should_invalidate=True):
        if hasattr(qs_or_action, 'all'):
            action = lambda: list(qs_or_action.all().cache())
        else:
            action = qs_or_action

        action()
        change()
        with self.assertNumQueries(1 if should_invalidate else 0):
            action()

    def test_target_invalidates_on_clear(self):
        self._template(
            self.bf.labels,
            lambda: self.bf.labels.clear()
        )

    def test_base_invalidates_on_clear(self):
        self._template(
            self.furious.brands,
            lambda: self.bf.labels.clear()
        )

    def test_granular_through_on_clear(self):
        through_qs = self.brand_cls.labels.through.objects.cache() \
                                                  .filter(brand=self.bs, label=self.slow)
        self._template(
            lambda: through_qs.get(),
            lambda: self.bf.labels.clear(),
            should_invalidate=False
        )

    def test_granular_target_on_clear(self):
        self._template(
            lambda: self.label_cls.objects.cache().get(pk=self.slow.pk),
            lambda: self.bf.labels.clear(),
            should_invalidate=False
        )

    def test_target_invalidates_on_add(self):
        self._template(
            self.bf.labels,
            lambda: self.bf.labels.add(self.slow)
        )

    def test_base_invalidates_on_add(self):
        self._template(
            self.slow.brands,
            lambda: self.bf.labels.add(self.slow)
        )

    def test_target_invalidates_on_remove(self):
        self._template(
            self.bf.labels,
            lambda: self.bf.labels.remove(self.furious)
        )

    def test_base_invalidates_on_remove(self):
        self._template(
            self.furious.brands,
            lambda: self.bf.labels.remove(self.furious)
        )


class MultiTableInheritanceWithM2MTest(M2MTests):
    brand_cls = PremiumBrand


class M2MThroughTests(M2MTests):
    brand_cls = BrandT
    label_cls = LabelT

    def setup_m2m(self):
        Labeling.objects.create(brand=self.bf, label=self.fast, tag=10)
        Labeling.objects.create(brand=self.bf, label=self.furious, tag=11)
        Labeling.objects.create(brand=self.bs, label=self.slow, tag=20)
        Labeling.objects.create(brand=self.bs, label=self.furious, tag=21)

    # No add and remove methods for explicit through models
    test_target_invalidates_on_add = None
    test_base_invalidates_on_add = None
    test_target_invalidates_on_remove = None
    test_base_invalidates_on_remove = None

    def test_target_invalidates_on_create(self):
        self._template(
            self.bf.labels,
            lambda: Labeling.objects.create(brand=self.bf, label=self.slow, tag=1)
        )

    def test_base_invalidates_on_create(self):
        self._template(
            self.slow.brands,
            lambda: Labeling.objects.create(brand=self.bf, label=self.slow, tag=1)
        )

    def test_target_invalidates_on_delete(self):
        self._template(
            self.bf.labels,
            lambda: Labeling.objects.get(brand=self.bf, label=self.furious).delete()
        )

    def test_base_invalidates_on_delete(self):
        self._template(
            self.furious.brands,
            # lambda: Labeling.objects.filter(brand=self.bf, label=self.furious).delete()
            lambda: Labeling.objects.get(brand=self.bf, label=self.furious).delete()
        )


class ProxyTests(BaseTestCase):
    def test_30(self):
        list(VideoProxy.objects.cache())
        Video.objects.create(title='Pulp Fiction')

        with self.assertNumQueries(1):
            list(VideoProxy.objects.cache())

    def test_30_reversed(self):
        list(Video.objects.cache())
        VideoProxy.objects.create(title='Pulp Fiction')

        with self.assertNumQueries(1):
            list(Video.objects.cache())

    @unittest.expectedFailure
    def test_interchange(self):
        list(Video.objects.cache())

        with self.assertNumQueries(0):
            list(VideoProxy.objects.cache())

    def test_148_invalidate_from_non_cached_proxy(self):
        video = Video.objects.create(title='Pulp Fiction')
        Video.objects.cache().get(title=video.title)
        NonCachedVideoProxy.objects.get(id=video.id).delete()

        with self.assertRaises(Video.DoesNotExist):
            Video.objects.cache().get(title=video.title)

    def test_148_reverse(self):
        media = NonCachedMedia.objects.create(title='Pulp Fiction')
        MediaProxy.objects.cache().get(title=media.title)
        NonCachedMedia.objects.get(id=media.id).delete()

        with self.assertRaises(NonCachedMedia.DoesNotExist):
            MediaProxy.objects.cache().get(title=media.title)

    def test_proxy_caching(self):
        video = Video.objects.create(title='Pulp Fiction')
        self.assertEqual(type(Video.objects.cache().get(pk=video.pk)),
                         Video)
        self.assertEqual(type(VideoProxy.objects.cache().get(pk=video.pk)),
                         VideoProxy)

    def test_proxy_caching_reversed(self):
        video = Video.objects.create(title='Pulp Fiction')
        self.assertEqual(type(VideoProxy.objects.cache().get(pk=video.pk)),
                         VideoProxy)
        self.assertEqual(type(Video.objects.cache().get(pk=video.pk)),
                         Video)


class MultitableInheritanceTests(BaseTestCase):
    @unittest.expectedFailure
    def test_sub_added(self):
        media_count = Media.objects.cache().count()
        Movie.objects.create(name="Matrix", year=1999)

        with self.assertNumQueries(1):
            self.assertEqual(Media.objects.cache().count(), media_count + 1)

    @unittest.expectedFailure
    def test_base_changed(self):
        matrix = Movie.objects.create(name="Matrix", year=1999)
        list(Movie.objects.cache())

        media = Media.objects.get(pk=matrix.pk)
        media.name = "Matrix (original)"
        media.save()

        with self.assertNumQueries(1):
            list(Movie.objects.cache())


class SimpleCacheTests(BaseTestCase):
    def test_cached(self):
        calls = [0]

        @cached(timeout=100)
        def get_calls(_):
            calls[0] += 1
            return calls[0]

        self.assertEqual(get_calls(1), 1)
        self.assertEqual(get_calls(1), 1)
        self.assertEqual(get_calls(2), 2)
        get_calls.invalidate(2)
        self.assertEqual(get_calls(2), 3)

        get_calls.key(2).delete()
        self.assertEqual(get_calls(2), 4)

        get_calls.key(2).set(42)
        self.assertEqual(get_calls(2), 42)

    def test_cached_view(self):
        calls = [0]

        @cached_view(timeout=100)
        def get_calls(request):
            calls[0] += 1
            return calls[0]

        factory = RequestFactory()
        r1 = factory.get('/hi')
        r2 = factory.get('/hi')
        r2.META['REMOTE_ADDR'] = '10.10.10.10'
        r3 = factory.get('/bye')

        self.assertEqual(get_calls(r1), 1) # cache
        self.assertEqual(get_calls(r1), 1) # hit
        self.assertEqual(get_calls(r2), 1) # hit, since only url is considered
        self.assertEqual(get_calls(r3), 2) # miss

        get_calls.invalidate(r1)
        self.assertEqual(get_calls(r1), 3) # miss

        # Can pass uri to invalidate
        get_calls.invalidate(r1.build_absolute_uri())
        self.assertEqual(get_calls(r1), 4) # miss


class DbAgnosticTests(BaseTestCase):
    def test_db_agnostic_by_default(self):
        list(DbAgnostic.objects.cache())

        with self.assertNumQueries(0, using='slave'):
            list(DbAgnostic.objects.cache().using('slave'))

    def test_db_agnostic_disabled(self):
        list(DbBinded.objects.cache())

        # HACK: This prevents initialization queries to break .assertNumQueries() in MySQL.
        #       Also there is no .ensure_connection() in older Djangos, thus it's even uglier.
        # TODO: remove in Django 1.10
        connections['slave'].cursor().close()

        with self.assertNumQueries(1, using='slave'):
            list(DbBinded.objects.cache().using('slave'))


@unittest.skipIf(connection.settings_dict['ENGINE'] != 'django.contrib.gis.db.backends.postgis',
                 "Only for PostGIS")
class GISTests(BaseTestCase):
    def test_invalidate_model_with_geometry(self):
        geom = Geometry()
        geom.save()
        # Raises ValueError if this doesn't work
        invalidate_obj(geom)


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
        get_calls = _make_inc(cached_as(Category.objects.filter(title='test')))
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
                    with before('redis.StrictRedis.brpoplpush', lambda *a, **kw: locked.set()):
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


class SettingsTests(TestCase):
    def test_override(self):
        from cacheops.conf import settings

        self.assertTrue(settings.CACHEOPS_ENABLED)

        with self.settings(CACHEOPS_ENABLED=False):
            self.assertFalse(settings.CACHEOPS_ENABLED)


# Utilities

def _make_inc(deco=lambda x: x):
    calls = [0]

    @deco
    def inc(_=None, **kw):
        calls[0] += 1
        return calls[0]

    inc.get = lambda: calls[0]
    return inc

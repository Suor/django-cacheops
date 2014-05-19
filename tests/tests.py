import os, re, copy
try:
    import unittest2 as unittest
except ImportError:
    import unittest

import django
from django.test import TestCase
from django.contrib.auth.models import User
from django.template import Context, Template

from cacheops import invalidate_all, invalidate_model, invalidate_obj, cached
from .models import *


class BaseTestCase(TestCase):
    def setUp(self):
        super(BaseTestCase, self).setUp()
        invalidate_all()


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

    def test_db_column(self):
        e = Extra.objects.cache().get(tag=5)
        e.save()

    def test_fk_to_db_column(self):
        e = Extra.objects.cache().get(to_tag__tag=5)
        e.save()

        with self.assertNumQueries(1):
            Extra.objects.cache().get(to_tag=5)

    def test_expressions(self):
        from django.db.models import F
        queries = (
            {'tag': F('tag')},
            {'tag': F('to_tag')},
            {'tag': F('to_tag') * 2},
            {'tag': F('to_tag') + (F('tag') / 2)},
        )
        if hasattr(F, 'bitor'):
            queries += (
                {'tag': F('tag').bitor(5)},
                {'tag': F('to_tag').bitor(5)},
                {'tag': F('tag').bitor(5) + 1},
                {'tag': F('tag').bitor(5) * F('to_tag').bitor(5)}
            )
        count = len(queries)
        for c in (count, 0):
            with self.assertNumQueries(c):
                for q in queries:
                    Extra.objects.cache().filter(**q).count()

    def test_combine(self):
        qs = Post.objects.filter(pk__in=[1, 2]) & Post.objects.all()
        self.assertEqual(list(qs.cache()), list(qs))

        qs = Post.objects.filter(pk__in=[1, 2]) | Post.objects.none()
        self.assertEqual(list(qs.cache()), list(qs))


from datetime import date, datetime, time

class WeirdTests(BaseTestCase):
    def _template(self, field, value, invalidation=True):
        qs = Weird.objects.cache().filter(**{field: value})
        count = qs.count()

        Weird.objects.create(**{field: value})

        if invalidation:
            with self.assertNumQueries(1):
                self.assertEqual(qs.count(), count + 1)

    def test_date(self):
        self._template('date_field', date.today())

    def test_datetime(self):
        self._template('datetime_field', datetime.now())

    def test_time(self):
        self._template('time_field', time(10, 30))

    def test_list(self):
        self._template('list_field', [1, 2])

    def test_custom(self):
        self._template('custom_field', CustomValue('some'))

    def test_weird_custom(self):
        class WeirdCustom(CustomValue):
            def __str__(self):
                return 'other'
        self._template('custom_field', WeirdCustom('some'))

    def test_custom_query(self):
        import cacheops.query
        try:
            cacheops.query.STRICT_STRINGIFY = False
            list(Weird.customs.cache())
        finally:
            cacheops.query.STRICT_STRINGIFY = True


class TemplateTests(BaseTestCase):
    @unittest.skipIf(django.VERSION < (1, 4), "not supported Django prior to 1.4")
    def test_cached(self):
        counts = {'a': 0, 'b': 0}
        def inc_a():
            counts['a'] += 1
            return ''
        def inc_b():
            counts['b'] += 1
            return ''

        t = Template("""
            {% load cacheops %}
            {% cached 60 'a' %}.a{{ a }}{% endcached %}
            {% cached 60 'a' %}.a{{ a }}{% endcached %}
            {% cached 60 'a' 'variant' %}.a{{ a }}{% endcached %}
            {% cached timeout=60 fragment_name='b' %}.b{{ b }}{% endcached %}
        """)

        s = t.render(Context({'a': inc_a, 'b': inc_b}))
        self.assertEqual(re.sub(r'\s+', '', s), '.a.a.a.b')
        self.assertEqual(counts, {'a': 2, 'b': 1})

    @unittest.skipIf(django.VERSION < (1, 4), "not supported Django prior to 1.4")
    def test_cached_as(self):
        counts = {'a': 0}
        def inc_a():
            counts['a'] += 1
            return ''

        qs = Post.objects.all()

        t = Template("""
            {% load cacheops %}
            {% cached_as qs 0 'a' %}.a{{ a }}{% endcached_as %}
            {% cached_as qs timeout=60 fragment_name='a' %}.a{{ a }}{% endcached_as %}
            {% cached_as qs fragment_name='a' timeout=60 %}.a{{ a }}{% endcached_as %}
        """)

        s = t.render(Context({'a': inc_a, 'qs': qs}))
        self.assertEqual(re.sub(r'\s+', '', s), '.a.a.a')
        self.assertEqual(counts['a'], 1)

        t.render(Context({'a': inc_a, 'qs': qs}))
        self.assertEqual(counts['a'], 1)

        invalidate_model(Post)
        t.render(Context({'a': inc_a, 'qs': qs}))
        self.assertEqual(counts['a'], 2)


class IssueTests(BaseTestCase):
    fixtures = ['basic']

    def setUp(self):
        user = User.objects.create(username='Suor')
        Profile.objects.create(pk=2, user=user, tag=10)
        super(IssueTests, self).setUp()

    def test_16(self):
        p = Profile.objects.cache().get(user__id__exact=1)
        p.save()

        with self.assertNumQueries(1):
            Profile.objects.cache().get(user=1)

    def test_29(self):
        Brand.objects.exclude(labels__in=[1, 2, 3]).cache().count()

    def test_39(self):
        list(Point.objects.filter(x=7).cache())

    def test_45(self):
        m = CacheOnSaveModel(title="test")
        m.save()

        with self.assertNumQueries(0):
            CacheOnSaveModel.objects.cache().get(pk=m.pk)

    def test_54(self):
        qs = Category.objects.all()
        list(qs) # force load objects to quesryset cache
        qs.count()

    def test_56(self):
        Post.objects.exclude(extra__in=[1, 2]).cache().count()

    def test_57(self):
        list(Post.objects.filter(category__in=Category.objects.nocache()).cache())

    def test_58(self):
        list(Post.objects.cache().none())

    def test_62(self):
        # setup
        product = Product.objects.create(name='62')
        ProductReview.objects.create(product=product, status=0)
        ProductReview.objects.create(product=None, status=0)

        # Test related manager filtering works, .get() will throw MultipleObjectsReturned if not
        # The bug is related manager not respected when .get() is called
        product.reviews.get(status=0)

    def test_70(self):
        Contained(name="aaa").save()
        contained_obj = Contained.objects.get(name="aaa")
        GenericContainer(content_object=contained_obj, name="bbb").save()

        qs = Contained.objects.cache().filter(containers__name="bbb")
        list(qs)

    def test_82(self):
        list(copy.deepcopy(Post.objects.all()).cache())


@unittest.skipIf(not os.environ.get('LONG'), "Too long")
class LongTests(BaseTestCase):
    fixtures = ['basic']

    def test_big_invalidation(self):
        for x in range(8000):
            list(Category.objects.cache().exclude(pk=x))

        c = Category.objects.get(pk=1)
        invalidate_obj(c) # lua unpack() fails with 8000 keys, workaround works


class LocalGetTests(BaseTestCase):
    def setUp(self):
        Local.objects.create(pk=1)
        super(LocalGetTests, self).setUp()

    def test_unhashable_args(self):
        Local.objects.cache().get(pk__in=[1, 2])


class RelatedTests(BaseTestCase):
    fixtures = ['basic']

    def test_related_invalidation(self):
        list(Post.objects.filter(category__title='Django').cache())

        c = Category.objects.get(title='Django')
        c.title = 'Forget it'
        c.save()

        with self.assertNumQueries(1):
            posts = Post.objects.filter(category__title='Django').cache()
            self.assertEqual(len(posts), 0)


class M2MTests(BaseTestCase):
    def setUp(self):
        self.bf = Brand.objects.create()
        self.bs = Brand.objects.create()

        self.fast = Label.objects.create(text='fast')
        self.slow = Label.objects.create(text='slow')
        self.furious = Label.objects.create(text='furios')

        self.bf.labels.add(self.fast, self.furious)
        self.bs.labels.add(self.slow, self.furious)

        super(M2MTests, self).setUp()

    def test_target_invalidates(self):
        list(self.bf.labels.cache())
        self.bf.labels.clear()

        with self.assertNumQueries(1):
            list(self.bf.labels.cache())

    def test_base_invalidates(self):
        list(self.furious.brands.cache())
        self.bf.labels.clear()

        with self.assertNumQueries(1):
            list(self.furious.brands.cache())

    def test_granular_through(self):
        through_qs = Brand.labels.through.objects.cache().filter(brand=self.bs, label=self.slow)
        through_qs.get()

        self.bf.labels.clear()

        with self.assertNumQueries(0):
            through_qs.get()

    def test_granular_target(self):
        Label.objects.cache().get(pk=self.slow.pk)

        self.bf.labels.clear()

        with self.assertNumQueries(0):
            Label.objects.cache().get(pk=self.slow.pk)


class M2MThroughTests(BaseTestCase):
    def setUp(self):
        self.suor = User.objects.create(username='Suor')
        self.peterdds = User.objects.create(username='peterdds')
        self.photo = Photo.objects.create()
        PhotoLike.objects.create(user=self.suor, photo=self.photo)
        super(M2MThroughTests, self).setUp()

    @unittest.expectedFailure
    def test_44(self):
        make_query = lambda: list(self.photo.liked_user.order_by('id').cache())
        self.assertEqual(make_query(), [self.suor])

        # query cache won't be invalidated on this create, since PhotoLike is through model
        PhotoLike.objects.create(user=self.peterdds, photo=self.photo)
        self.assertEqual(make_query(), [self.suor, self.peterdds])

    def test_44_workaround(self):
        make_query = lambda: list(self.photo.liked_user.order_by('id').cache())
        self.assertEqual(make_query(), [self.suor])

        PhotoLike.objects.create(user=self.peterdds, photo=self.photo)
        invalidate_obj(self.peterdds)
        self.assertEqual(make_query(), [self.suor, self.peterdds])


# Tests for proxy models, see #30
class ProxyTests(BaseTestCase):
    def test_30(self):
        proxies = list(VideoProxy.objects.cache())
        Video.objects.create(title='Pulp Fiction')

        with self.assertNumQueries(1):
            list(VideoProxy.objects.cache())

    def test_30_reversed(self):
        proxies = list(Video.objects.cache())
        VideoProxy.objects.create(title='Pulp Fiction')

        with self.assertNumQueries(1):
            list(Video.objects.cache())

    @unittest.expectedFailure
    def test_interchange(self):
        proxies = list(Video.objects.cache())

        with self.assertNumQueries(0):
            list(VideoProxy.objects.cache())


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
        def get_calls(x):
            calls[0] += 1
            return calls[0]

        self.assertEqual(get_calls(1), 1)
        self.assertEqual(get_calls(1), 1)
        self.assertEqual(get_calls(2), 2)
        get_calls.invalidate(2)
        self.assertEqual(get_calls(2), 3)


class DbAgnosticTests(BaseTestCase):
    @unittest.skipIf(django.VERSION < (1, 4), "not supported Django prior to 1.4")
    def test_db_agnostic_by_default(self):
        list(DbAgnostic.objects.cache())

        with self.assertNumQueries(0, using='slave'):
            list(DbAgnostic.objects.cache().using('slave'))

    @unittest.skipIf(django.VERSION < (1, 4), "not supported Django prior to 1.4")
    def test_db_agnostic_disabled(self):
        list(DbBinded.objects.cache())

        with self.assertNumQueries(1, using='slave'):
            list(DbBinded.objects.cache().using('slave'))

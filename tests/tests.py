from contextlib import contextmanager
from functools import reduce
import operator
import re
import platform
import unittest
from unittest import mock

import django
from django.db import connection
from django.db import DEFAULT_DB_ALIAS
from django.test import override_settings
from django.test.client import RequestFactory
from django.template import Context, Template
from django.db.models import F, Count, Max, OuterRef, Sum, Subquery, Exists, Q
from django.db.models.expressions import RawSQL

from cacheops import invalidate_model, invalidate_obj, \
    cached, cached_view, cached_as, cached_view_as
from cacheops import invalidate_fragment
from cacheops.query import invalidate_m2o
from cacheops.templatetags.cacheops import register

decorator_tag = register.decorator_tag
from .models import *  # noqa
from .utils import BaseTestCase, make_inc


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
        with self.assertNumQueries(0):
            Post.objects.cache().filter(visible=True).count()

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

    def test_subquery(self):
        categories = Category.objects.cache().filter(title='Django').only('id')
        Post.objects.cache().filter(category__in=Subquery(categories)).count()

    def test_rawsql(self):
        Post.objects.cache().filter(category__in=RawSQL("select 1", ())).count()


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


class DecoratorTests(BaseTestCase):
    def test_cached_as_model(self):
        get_calls = make_inc(cached_as(Category))

        self.assertEqual(get_calls(), 1)      # miss
        self.assertEqual(get_calls(), 1)      # hit
        Category.objects.create(title='test') # invalidate
        self.assertEqual(get_calls(), 2)      # miss

    def test_cached_as_cond(self):
        get_calls = make_inc(cached_as(Category.objects.filter(title='test')))

        self.assertEqual(get_calls(), 1)      # cache
        Category.objects.create(title='miss') # don't invalidate
        self.assertEqual(get_calls(), 1)      # hit
        Category.objects.create(title='test') # invalidate
        self.assertEqual(get_calls(), 2)      # miss

    def test_cached_as_obj(self):
        c = Category.objects.create(title='test')
        get_calls = make_inc(cached_as(c))

        self.assertEqual(get_calls(), 1)      # cache
        Category.objects.create(title='miss') # don't invalidate
        self.assertEqual(get_calls(), 1)      # hit
        c.title = 'new'
        c.save()                              # invalidate
        self.assertEqual(get_calls(), 2)      # miss

    def test_cached_as_depends_on_args(self):
        get_calls = make_inc(cached_as(Category))

        self.assertEqual(get_calls(1), 1)      # cache
        self.assertEqual(get_calls(1), 1)      # hit
        self.assertEqual(get_calls(2), 2)      # miss

    def test_cached_as_depends_on_two_models(self):
        get_calls = make_inc(cached_as(Category, Post))
        c = Category.objects.create(title='miss')
        p = Post.objects.create(title='New Post', category=c)

        self.assertEqual(get_calls(1), 1)      # cache
        c.title = 'new title'
        c.save()                               # invalidate by Category
        self.assertEqual(get_calls(1), 2)      # miss and cache
        p.title = 'new title'
        p.save()                               # invalidate by Post
        self.assertEqual(get_calls(1), 3)      # miss and cache

    def test_cached_as_keep_fresh(self):
        c = Category.objects.create(title='test')
        calls = [0]

        @cached_as(c, keep_fresh=True)
        def get_calls(_=None, **kw):
            # Invalidate during first run
            if calls[0] < 1:
                invalidate_obj(c)
            calls[0] += 1
            return calls[0]

        self.assertEqual(get_calls(), 1)      # miss, stale result not cached.
        self.assertEqual(get_calls(), 2)      # miss and cache
        self.assertEqual(get_calls(), 2)      # hit

    def test_cached_view_as(self):
        get_calls = make_inc(cached_view_as(Category))

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


from datetime import date, time
from django.utils import timezone

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
        self._template('datetime_field', timezone.now().replace(microsecond=0))

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

    def test_json(self):
        list(TaggedPost.objects.filter(meta__author='Suor'))


class TemplateTests(BaseTestCase):
    def assertRendersTo(self, template, context, result):
        s = template.render(Context(context))
        self.assertEqual(re.sub(r'\s+', '', s), result)

    def test_cached(self):
        inc_a = make_inc()
        inc_b = make_inc()
        t = Template("""
            {% load cacheops %}
            {% cached 60 'a' %}.a{{ a }}{% endcached %}
            {% cached 60 'a' %}.a{{ a }}{% endcached %}
            {% cached 60 'a' 'variant' %}.a{{ a }}{% endcached %}
            {% cached timeout=60 fragment_name='b' %}.b{{ b }}{% endcached %}
        """)

        self.assertRendersTo(t, {'a': inc_a, 'b': inc_b}, '.a1.a1.a2.b1')

    def test_invalidate_fragment(self):
        inc = make_inc()
        t = Template("""
            {% load cacheops %}
            {% cached 60 'a' %}.{{ inc }}{% endcached %}
        """)

        self.assertRendersTo(t, {'inc': inc}, '.1')

        invalidate_fragment('a')
        self.assertRendersTo(t, {'inc': inc}, '.2')

    def test_cached_as(self):
        inc = make_inc()
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

        inc = make_inc()
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

        inc = make_inc()
        t = Template("""
            {% load cacheops %}
            {% my_cached %}.{{ inc }}{% endmy_cached %}
            {% my_cached %}.{{ inc }}{% endmy_cached %}
        """)

        self.assertRendersTo(t, {'inc': inc, 'flag': True}, '.1.1')
        self.assertRendersTo(t, {'inc': inc, 'flag': False}, '.2.3')

    def test_jinja2(self):
        from jinja2 import Environment
        env = Environment(extensions=['cacheops.jinja2.cache'])
        t = env.from_string('Hello, {% cached %}{{ name }}{% endcached %}')
        t.render(name='Alex')


class IssueTests(BaseTestCase):
    databases = ('default', 'slave')
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
        # This will trigger a bug invalidating brands querying them by label id.
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

    @unittest.skipIf(django.VERSION < (3, 0), "Fixed in Django 3.0")
    def test_312(self):
        device = Device.objects.create()

        # query by 32bytes uuid
        d = Device.objects.cache().get(uid=device.uid.hex)

        # test invalidation
        d.model = 'new model'
        d.save()

        with self.assertNumQueries(1):
            changed_device = Device.objects.cache().get(uid=device.uid.hex)
            self.assertEqual(d.model, changed_device.model)

    def test_316(self):
        Category.objects.cache().annotate(num=Count('posts')).aggregate(total=Sum('num'))

    @unittest.expectedFailure
    def test_348(self):
        foo = Foo.objects.create()
        bar = Bar.objects.create(foo=foo)

        bar = Bar.objects.cache().get(pk=bar.pk)
        bar.foo.delete()

        bar = Bar.objects.cache().get(pk=bar.pk)
        bar.foo  # fails here since we try to fetch Foo instance by cached id

    def test_352(self):
        CombinedFieldModel.objects.create()
        list(CombinedFieldModel.objects.cache().all())

    def test_353(self):
        foo = Foo.objects.create()
        bar = Bar.objects.create()

        self.assertEqual(Foo.objects.cache().filter(bar__isnull=True).count(), 1)
        bar.foo = foo
        bar.save()
        self.assertEqual(Foo.objects.cache().filter(bar__isnull=True).count(), 0)

    @unittest.skipIf(django.VERSION < (3, 0), "Supported from Django 3.0")
    def test_359(self):
        post_filter = Exists(Post.objects.all())
        len(Category.objects.filter(post_filter).cache())

    def test_365(self):
        """
        Check that an annotated Subquery is automatically invalidated.
        """
        # Retrieve all Categories and annotate the ID of the most recent Post for each
        newest_post = Post.objects.filter(category=OuterRef('pk')).order_by('-pk').values('pk')
        categories = Category.objects.cache().annotate(newest_post=Subquery(newest_post[:1]))

        # Create a new Post in the first Category
        post = Post(category=categories[0], title='Foo')
        post.save()

        # Retrieve Categories again, and check that the newest post ID is correct
        categories = Category.objects.cache().annotate(newest_post=Subquery(newest_post[:1]))
        self.assertEqual(categories[0].newest_post, post.pk)

    @unittest.skipIf(platform.python_implementation() == "PyPy", "dill doesn't do that in PyPy")
    def test_385(self):
        Client.objects.create(name='Client Name')

        with self.assertRaisesRegex(AttributeError, "local object"):
            Client.objects.filter(name='Client Name').cache().first()

        invalidate_model(Client)

        with override_settings(CACHEOPS_SERIALIZER='dill'):
            with self.assertNumQueries(1):
                Client.objects.filter(name='Client Name').cache().first()
                Client.objects.filter(name='Client Name').cache().first()

    def test_387(self):
        post = Post.objects.defer("visible").last()
        post.delete()

    def test_407(self):
        brand = Brand.objects.create(pk=1)
        brand.labels.set([Label.objects.create()])
        assert len(Label.objects.filter(brands=1).cache()) == 1  # Cache it

        brand.delete()  # Invalidation expected after deletion

        with self.assertNumQueries(1):
            self.assertEqual(len(Label.objects.filter(brands=1).cache()), 0)

    def test_407_reverse(self):
        brand = Brand.objects.create(pk=1)
        label = Label.objects.create(pk=1)
        brand.labels.set([label])
        assert len(Brand.objects.filter(labels=1).cache()) == 1  # Cache it

        label.delete()  # Invalidation expected after deletion

        with self.assertNumQueries(1):
            self.assertEqual(len(Brand.objects.filter(labels=1).cache()), 0)

    @mock.patch('cacheops.query.invalidate_dict')
    def test_430(self, mock_invalidate_dict):
        media_type = MediaType.objects.create(
            name="some type"
        )
        movie = Movie.objects.create(
            year=2022,
            media_type=media_type,
        )
        Scene.objects.create(
            name="first scene",
            movie=movie,
        )
        invalidate_m2o(Movie, movie)

        obj_movie_dict = mock_invalidate_dict.call_args[0][1]
        self.assertFalse(isinstance(obj_movie_dict['movie_id'], Media))
        self.assertTrue(isinstance(obj_movie_dict['movie_id'], int))

    def test_430_no_error_raises(self):
        media_type = MediaType.objects.create(
            name="some type"
        )
        movie = Movie.objects.create(
            year=2022,
            media_type=media_type,
        )
        Scene.objects.create(
            name="first scene",
            movie=movie,
        )
        # no error raises on delete
        media_type.delete()

    def test_480(self):
        orm_lookups = ['title__icontains', 'category__title__icontains']
        search_terms = ['1', '2', '3', '4', '5', '6', '7', '8', '9', '10', '11', '12', '13']
        queryset = Post.objects.filter(visible=True)
        conditions = []
        for search_term in search_terms:
            queries = [
                models.Q(**{orm_lookup: search_term})
                for orm_lookup in orm_lookups
            ]
            conditions.append(reduce(operator.or_, queries))
        list(queryset.filter(reduce(operator.and_, conditions)).cache())

    @unittest.skipIf(connection.vendor != 'postgresql', "Only for PostgreSQL")
    def test_489(self):
        TaggedPost.objects.cache().filter(tags=[]).count()


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

    def test_aggregate_granular(self):
        cat1, cat2 = Category.objects.all()[:2]
        qs = Category.objects.cache().filter(id=cat1.id)
        qs.aggregate(posts_count=Count('posts'))
        # Test invalidation
        Post.objects.create(title='New One', category=cat2)
        with self.assertNumQueries(0):
            qs.aggregate(posts_count=Count('posts'))

    def test_new_alias(self):
        qs = Post.objects.cache()
        assert qs.aggregate(max=Max('category')) == {'max': 3}
        assert qs.aggregate(cat=Max('category')) == {'cat': 3}

    def test_filter(self):
        qs = Post.objects.cache()
        assert qs.aggregate(cnt=Count('category', filter=Q(category__gt=1))) == {'cnt': 2}
        assert qs.aggregate(cnt=Count('category', filter=Q(category__lt=3))) == {'cnt': 1}


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
        through_qs.get()
        self.bf.labels.clear()
        with self.assertNumQueries(0):
            through_qs.get()

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

    def test_siblings(self):
        list(VideoProxy.objects.cache())
        NonCachedVideoProxy.objects.create(title='Pulp Fiction')

        with self.assertNumQueries(1):
            list(VideoProxy.objects.cache())

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
        get_calls = make_inc(cached(timeout=100))

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
        get_calls = make_inc(cached_view(timeout=100))

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


@unittest.skipIf(connection.settings_dict['ENGINE'] != 'django.contrib.gis.db.backends.postgis',
                 "Only for PostGIS")
class GISTests(BaseTestCase):
    def test_invalidate_model_with_geometry(self):
        geom = Geometry()
        geom.save()
        # Raises ValueError if this doesn't work
        invalidate_obj(geom)


# NOTE: overriding cache prefix to separate invalidation sets by db.
@override_settings(CACHEOPS_PREFIX=lambda q: q.db)
class MultiDBInvalidationTests(BaseTestCase):
    databases = ('default', 'slave')
    fixtures = ['basic']

    @contextmanager
    def _control_counts(self):
        Category.objects.cache().count()
        Category.objects.using('slave').cache().count()

        yield
        with self.assertNumQueries(0):
            Category.objects.cache().count()
        with self.assertNumQueries(1, using='slave'):
            Category.objects.cache().using('slave').count()

    def test_save(self):
        # NOTE: not testing when old db != new db,
        #       how cacheops works in that situation is undefined at the moment
        with self._control_counts():
            obj = Category()
            obj.save(using='slave')

    def test_delete(self):
        obj = Category.objects.using('slave').create()
        with self._control_counts():
            obj.delete(using='slave')

    def test_bulk_create(self):
        with self._control_counts():
            Category.objects.using('slave').bulk_create([Category(title='New')])

    def test_invalidated_update(self):
        # NOTE: not testing router-based routing
        with self._control_counts():
            Category.objects.using('slave').invalidated_update(title='update')

    @mock.patch('cacheops.invalidation.invalidate_dict')
    def test_m2m_changed_call_invalidate(self, mock_invalidate_dict):
        label = Label.objects.create()
        brand = Brand.objects.create()
        brand.labels.add(label)
        mock_invalidate_dict.assert_called_with(mock.ANY, mock.ANY, using=DEFAULT_DB_ALIAS)

        label = Label.objects.using('slave').create()
        brand = Brand.objects.using('slave').create()
        brand.labels.add(label)
        mock_invalidate_dict.assert_called_with(mock.ANY, mock.ANY, using='slave')

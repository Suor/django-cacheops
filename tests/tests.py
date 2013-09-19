import unittest, random
from django.test import TestCase
from django.contrib.auth.models import User

from cacheops import invalidate_all, cached
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


class IssueTests(BaseTestCase):
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
        MachineBrand.objects.exclude(labels__in=[1,2,3]).cache().count()

    def test_39(self):
        list(Point.objects.filter(x=7).cache())

    def test_45(self):
        m = CacheOnSaveModel(title="test")
        m.save()

        with self.assertNumQueries(0):
            CacheOnSaveModel.objects.cache().get(pk=m.pk)


class LocalGetTests(BaseTestCase):
    def setUp(self):
        Local.objects.create(pk=1)
        super(LocalGetTests, self).setUp()

    def test_unhashable_args(self):
        Local.objects.cache().get(pk__in=[1, 2])


class ManyToManyTests(BaseTestCase):
    def setUp(self):
        self.suor = User.objects.create(username='Suor')
        self.peterdds = User.objects.create(username='peterdds')
        self.photo = Photo.objects.create()
        PhotoLike.objects.create(user=self.suor, photo=self.photo)
        super(ManyToManyTests, self).setUp()

    @unittest.expectedFailure
    def test_44(self):
        make_query = lambda: list(self.photo.liked_user.order_by('id').cache())
        self.assertEqual(make_query(), [self.suor])

        PhotoLike.objects.create(user=self.peterdds, photo=self.photo)
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


class DatabaseClusterSupportTests(BaseTestCase):

    @classmethod
    def setUpClass(cls):
        cls.qs = DatabaseClusterSupportModel.objects.cache()
        cls.default_cache_profile = cls.qs._cacheprofile.copy()

    def setUp(self):
        super(DatabaseClusterSupportTests, self).setUp()
        self.qs._cacheprofile = self.default_cache_profile.copy()

    def test_disable_by_default(self):
        list(self.qs.all())

        with self.assertNumQueries(0):
            list(self.qs.all())
        with self.assertNumQueries(0, using="slave"):
            list(self.qs.using("slave").all())

    def test_enable_by_cache_profile(self):
        self.qs._cacheprofile.update(fidelity=True)
        list(self.qs.all())

        with self.assertNumQueries(0):
            list(self.qs.all())
        with self.assertNumQueries(1, using="slave"):
            list(self.qs.using("slave").all())
        # Ensure that cache worked
        with self.assertNumQueries(0, using="slave"):
            list(self.qs.using("slave").all())

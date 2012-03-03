from django.test import TestCase

from cacheops.conf import redis_conn
from .models import Category, Post


class BaseTestCase(TestCase):
    def setUp(self):
        super(BaseTestCase, self).setUp()
        redis_conn.flushdb()


class BasicTests(BaseTestCase):
    def test_it_works(self):
        with self.assertNumQueries(1):
            cnt1 = Category.objects.cache().count()
            cnt2 = Category.objects.cache().count()
            self.assertEqual(cnt1, cnt2)

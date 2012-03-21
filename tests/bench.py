from django.test import TestCase

from cacheops import invalidate_all
from .models import Category, Post


class BaseTestCase(TestCase):
    def setUp(self):
        super(BaseTestCase, self).setUp()
        invalidate_all()


class BasicTests(BaseTestCase):
    # fixtures = ['basic']

    def test_it_works(self):
        print 'it works'

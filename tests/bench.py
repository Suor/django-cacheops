# from django.test import TestCase

from cacheops import invalidate_all
from .models import Category, Post


# class BaseTestCase(TestCase):
#     def setUp(self):
#         super(BaseTestCase, self).setUp()
#         invalidate_all()


# class BasicTests(BaseTestCase):
#     # fixtures = ['basic']

def do_count():
    return Category.objects.count()

def do_fetch():
    return list(Category.objects.all())

TESTS = [
    ('count_miss', {'prepare': invalidate_all, 'run': do_count}),
    ('count_hit',  {'prepare_once': do_count, 'run': do_count}),
    ('fetch_miss', {'prepare': invalidate_all, 'run': do_fetch}),
    ('fetch_hit',  {'prepare_once': do_fetch, 'run': do_fetch}),
]

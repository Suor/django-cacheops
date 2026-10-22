import unittest

import django
from django.db import models

from cacheops import cached_as
from cacheops.query import _local_get_cache

from .models import CacheOnSaveModel, Category, Extra, Local, Post
from .utils import BaseTestCase, make_inc


@unittest.skipUnless(django.VERSION >= (6, 1), "Fetch modes require Django 6.1")
class FetchModeTests(BaseTestCase):
    fixtures = ["basic"]

    def setUp(self):
        super().setUp()
        _local_get_cache.clear()
        self.addCleanup(_local_get_cache.clear)

    def test_default_mode_still_caches(self):
        with self.assertNumQueries(1):
            Category.objects.cache().get(pk=1)
            category = Category.objects.fetch_mode(models.FETCH_ONE).cache().get(pk=1)
        self.assertEqual(category.title, "Django")

    def test_raise_mode_does_not_reuse_default_cache(self):
        from django.core.exceptions import FieldFetchBlocked

        Category.objects.only("pk").cache().get(pk=1)
        with self.assertNumQueries(1):
            category = (
                Category.objects.only("pk")
                .fetch_mode(models.FETCH_RAISE)
                .cache()
                .get(pk=1)
            )
        with self.assertRaises(FieldFetchBlocked):
            category.title

    def test_default_mode_does_not_reuse_raise_cache(self):
        Category.objects.only("pk").fetch_mode(models.FETCH_RAISE).cache().get(pk=1)
        with self.assertNumQueries(1):
            category = Category.objects.only("pk").cache().get(pk=1)
        with self.assertNumQueries(1):
            self.assertEqual(category.title, "Django")

    def test_peer_mode_keeps_batching(self):
        for _ in range(2):
            with self.assertNumQueries(1):
                qs = Category.objects.only("pk").fetch_mode(models.FETCH_PEERS).cache()
                categories = list(qs)
            with self.assertNumQueries(1):
                titles = [category.title for category in categories]
            self.assertCountEqual(
                titles, ["Django", "Rails", "Perl", "Python", "Node.js"]
            )

    def test_local_get_does_not_mix_modes(self):
        local = Local.objects.create(tag=42)
        for mode in (models.FETCH_ONE, models.FETCH_RAISE, models.FETCH_PEERS):
            with self.subTest(mode=mode):
                with self.assertNumQueries(1):
                    fetched = Local.objects.fetch_mode(mode).cache().get(pk=local.pk)
                self.assertIs(fetched._state.fetch_mode, mode)
        with self.assertNumQueries(0):
            fetched = Local.objects.cache().get(pk=local.pk)
        self.assertIs(fetched._state.fetch_mode, models.FETCH_ONE)

    def test_cache_on_save_does_not_store_nondefault_mode(self):
        obj = CacheOnSaveModel.objects.create(title="old")
        obj = (
            CacheOnSaveModel.objects.fetch_mode(models.FETCH_RAISE)
            .cache()
            .get(pk=obj.pk)
        )
        obj.title = "new"
        obj.save(update_fields=["title"])

        with self.assertNumQueries(1):
            fetched = CacheOnSaveModel.objects.cache().get(pk=obj.pk)
        self.assertEqual(fetched.title, "new")
        self.assertIs(fetched._state.fetch_mode, models.FETCH_ONE)

    def test_save_does_not_fetch_unchanged_one_to_one(self):
        extra = Extra.objects.only("pk", "tag").fetch_mode(models.FETCH_RAISE).get(pk=1)
        extra.save(update_fields=["tag"])
        self.assertNotIn("post_id", extra.__dict__)

    def test_changed_one_to_one_still_invalidates(self):
        for field in ("post", "post_id"):
            with self.subTest(field=field):
                extra = Extra.objects.get(pk=1)
                old_post_id = extra.post_id
                new_post_id = 3 if old_post_id == 1 else 1
                old_calls = make_inc(
                    cached_as(Post.objects.filter(pk=old_post_id), extra=field)
                )
                new_calls = make_inc(
                    cached_as(Post.objects.filter(pk=new_post_id), extra=field)
                )
                self.assertEqual(old_calls(), 1)
                self.assertEqual(new_calls(), 1)
                extra.post_id = new_post_id
                extra.save(update_fields=[field])
                self.assertEqual(old_calls(), 2)
                self.assertEqual(new_calls(), 2)

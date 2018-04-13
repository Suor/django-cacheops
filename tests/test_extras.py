from django.test import TestCase
from django.test import override_settings

from cacheops.conf import settings


class SettingsTests(TestCase):
    def test_context_manager(self):
        self.assertTrue(settings.CACHEOPS_ENABLED)

        with self.settings(CACHEOPS_ENABLED=False):
            self.assertFalse(settings.CACHEOPS_ENABLED)

    @override_settings(CACHEOPS_ENABLED=False)
    def test_decorator(self):
        self.assertFalse(settings.CACHEOPS_ENABLED)


@override_settings(CACHEOPS_ENABLED=False)
class ClassOverrideSettingsTests(TestCase):
    def test_class(self):
        self.assertFalse(settings.CACHEOPS_ENABLED)

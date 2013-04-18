import os

from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from cacheops.simple import file_cache, FILE_CACHE_DIR


class Command(BaseCommand):
    help = 'Clean filebased cache'

    def handle(self, **options):
        os.system('find %s -type f \! -iname "\." -mmin +0 -delete' % FILE_CACHE_DIR)

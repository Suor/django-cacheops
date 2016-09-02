import os

from django.core.management.base import BaseCommand

from cacheops.conf import settings


class Command(BaseCommand):
    help = 'Clean filebased cache'

    def handle(self, **options):
        os.system('find %s -type f \! -iname "\." -mmin +0 -delete' % settings.FILE_CACHE_DIR)

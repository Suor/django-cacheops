import os

from django.core.management.base import BaseCommand

from cacheops.conf import settings


class Command(BaseCommand):
    help = 'Clean filebased cache'

    def add_arguments(self, parser):
        parser.add_argument('path', nargs='*', default=['default'])

    def handle(self, **options):
        for path in options['path']:
            if path == 'default':
                path = settings.FILE_CACHE_DIR
            os.system(r'find %s -type f \! -iname "\." -mmin +0 -delete' % path)

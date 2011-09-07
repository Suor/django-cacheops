import os

from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from cacheops.simple import file_cache

class Command(BaseCommand):
    help = 'Clean filebased cache'

    def handle(self, **options):
        self.verbose = options.get('verbosity', 0)
        
        if not settings.FILE_CACHE_DIR or not settings.HOME_DIR:
            raise ImproperlyConfigured(u'Set FILE_CACHE_DIR or HOME_DIR settings!')
        
        if not settings.FILE_CACHE_DIR.startswith('/tmp/'):
            raise ImproperlyConfigured(u'settings.FILE_CACHE_DIR is outside of tmp.. >_>')
        
        cachedir = settings.HOME_DIR + settings.FILE_CACHE_DIR
        os.system('find %s -type f \! -iname "\." -mmin +0 -delete' % cachedir)

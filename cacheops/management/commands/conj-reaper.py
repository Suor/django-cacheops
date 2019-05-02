import logging
import time
from os import getenv

import redis
from django.conf import settings
from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)
log_tmpl = 'Removed %s/%s cache keys from %s'


"""conj-reaper - A Django command to remove expired cache keys from invalidation sets.

cacheops saves each DB resultset cache key in a "conj set" so it can delete it later if it thinks it
should be invalidated due to a saved record with matching values. But the resultset caches time out
after 30 minutes, and their cache keys live in those conj sets forever!

So conj sets for frequent queries on tables that aren't updated often end up containing millions
of already-expired cache keys and maybe a few thousand actually useful ones, and block Redis for
multiple - or many - seconds when cacheops finally decides to invalidate them.

This is a Django command that loops forever scanning cacheops' conj keys for already-expired
cache keys and removing them.
"""


def env(key, default):
    return getenv('REAPER_' + key, default)


def set_batches(cache, key, batch_size):
    cursor = 0
    while True:
        cursor, items = cache.sscan(key, cursor, count=batch_size)
        if items:
            yield items
        if cursor == 0:
            break


class Command(BaseCommand):
    help = 'Removes expired cache keys from conj sets.'

    def add_arguments(self, parser):
        R = settings.CACHEOPS_REDIS
        parser.add_argument('--host', type=str, default=env('CACHE_HOST', R['host']))
        parser.add_argument('--port', type=int, default=env('CACHE_PORT', R['port']))
        parser.add_argument('--db', type=int, default=env('CACHE_DB', R['db']))
        parser.add_argument('-s', '--sleep', type=float, default=env('SLEEP', .01))
        parser.add_argument('-l', '--log-level', type=str, default=env('LOG_LEVEL', 'INFO'))
        parser.add_argument('-b', '--batch-size', type=int, default=env('BATCH_SIZE', 1000))
        parser.add_argument('-m', '--min-conj-set-size', type=int,
                            default=env('MIN_CONJ_SET_SIZE', 1000))

    def handle(self, host=None, port=None, db=None, sleep=None,
               log_level=None, batch_size=None, min_conj_set_size=None, **kwargs):
        """Scan the keys in a Redis DB in batches and remove expired cache keys from conj keys."""
        logger.setLevel(log_level)
        cache = redis.Redis(host, port, db)
        while True:
            logger.info('Starting scan for large conj sets')
            for conj in cache.scan_iter('conj:*', count=batch_size):
                total = cache.scard(conj)
                if total < min_conj_set_size:
                    continue
                logger.debug('Found %s cache keys in %s, scanning for expired keys', total, conj)
                self.scan_conj_set(cache, conj, batch_size, sleep)
            time.sleep(10)  # So we don't just spin continuously on an empty cache

    def scan_conj_set(self, cache, conj, batch_size, sleep):
        """Scan the cache keys in a conj set in batches and remove any that have expired."""
        count, removed = 0, 0
        for keys in set_batches(cache, conj, batch_size):
            count += len(keys)
            values = cache.mget(keys)
            expired = [k for v, k in zip(values, keys) if not v]
            if expired:
                cache.srem(conj, *expired)
                removed += len(expired)
                logger.debug(log_tmpl, removed, count, conj)
            time.sleep(sleep)
        if removed:
            cache.execute_command('MEMORY PURGE')
        if removed and not logger.isEnabledFor(logging.DEBUG):
            logger.info(log_tmpl, removed, count, conj)

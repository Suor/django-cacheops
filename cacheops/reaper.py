import logging

from django.db import DEFAULT_DB_ALIAS

from .redis import redis_client
from .sharding import get_prefix

logger = logging.getLogger(__name__)


def reap_conjs(
    chunk_size: int = 1000,
    min_conj_set_size: int = 1000,
    using=DEFAULT_DB_ALIAS,
    dry_run: bool = False,
):
    """
    Remove expired cache keys from invalidation sets.

    Cacheops saves each DB resultset cache key in a "conj set" so it can delete it later if it
    thinks it should be invalidated due to a saved record with matching values. But the resultset
    caches time out after 30 minutes, and their cache keys live in those conj sets forever!

    So conj sets for frequent queries on tables that aren't updated often end up containing
    millions of already-expired cache keys and maybe a few thousand actually useful ones,
    and block Redis for multiple - or many - seconds when cacheops finally decides
    to invalidate them.

    This function scans cacheops' conj keys for already-expired cache keys and removes them.
    """
    logger.info('Starting scan for large conj sets')
    prefix = get_prefix(dbs=[using])
    for conj_key in redis_client.scan_iter(prefix + 'conj:*', count=chunk_size):
        total = redis_client.scard(conj_key)
        if total < min_conj_set_size:
            continue
        logger.info('Found %s cache keys in %s, scanning for expired keys', total, conj_key)
        _clear_conj_key(conj_key, chunk_size, dry_run)
    logger.info('Done scan for large conj sets')


def _clear_conj_key(conj_key: bytes, chunk_size: int, dry_run: bool):
    """Scan the cache keys in a conj set in batches and remove any that have expired."""
    count, removed = 0, 0
    for keys in _iter_keys_chunk(chunk_size, conj_key):
        count += len(keys)
        values = redis_client.mget(keys)
        expired = [k for k, v in zip(keys, values) if not v]
        if expired:
            if not dry_run:
                redis_client.srem(conj_key, *expired)
            removed += len(expired)
            logger.info('Removed %s/%s cache keys from %s', removed, count, conj_key)
    if removed and not dry_run:
        redis_client.execute_command('MEMORY PURGE')


def _iter_keys_chunk(chunk_size, key):
    cursor = 0
    while True:
        cursor, items = redis_client.sscan(key, cursor, count=chunk_size)
        if items:
            yield items
        if cursor == 0:
            break

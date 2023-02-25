from contextlib import contextmanager
import hashlib
import json

from .conf import settings
from .redis import redis_client, handle_connection_failure, load_script
from .transaction import transaction_states


LOCK_TIMEOUT = 60


@handle_connection_failure
def cache_thing(prefix, cache_key, data, cond_dnfs, timeout, dbs=(), precall_key='',
                expected_checksum=''):
    """
    Writes data to cache and creates appropriate invalidators.

    If precall_key is not the empty string, the data will only be cached if the
    precall_key is set to avoid caching stale data.

    If expected_checksum is set and does not match the actual one then cache won't be written.
    """
    # Could have changed after last check, sometimes superficially
    if transaction_states.is_dirty(dbs):
        return

    if settings.CACHEOPS_INSIDEOUT:
        schemes = dnfs_to_schemes(cond_dnfs)
        conj_keys = dnfs_to_conj_keys(prefix, cond_dnfs)
        return load_script('cache_thing_insideout')(
            keys=[prefix, cache_key],
            args=[
                settings.CACHEOPS_SERIALIZER.dumps(data),
                json.dumps(schemes),
                json.dumps(conj_keys),
                timeout,
                expected_checksum,
            ]
        )
    else:
        if prefix and precall_key == "":
            precall_key = prefix
        load_script('cache_thing')(
            keys=[prefix, cache_key, precall_key],
            args=[
                settings.CACHEOPS_SERIALIZER.dumps(data),
                json.dumps(cond_dnfs, default=str),
                timeout
            ]
        )


@contextmanager
def getting(key, cond_dnfs, prefix, lock=False):
    if not lock:
        yield _read(key, cond_dnfs, prefix)
    else:
        locked = False
        try:
            data = _get_or_lock(key, cond_dnfs, prefix)
            locked = data is None
            yield data
        finally:
            if locked:
                _release_lock(key)


@handle_connection_failure
def _read(key, cond_dnfs, prefix):
    if not settings.CACHEOPS_INSIDEOUT:
        return redis_client.get(key)

    conj_keys = dnfs_to_conj_keys(prefix, cond_dnfs)
    coded, *stamps = redis_client.mget(key, *conj_keys)
    if coded is None or coded == b'LOCK':
        return coded

    if None in stamps:
        redis_client.unlink(key)
        return

    stamp_checksum, data = coded.split(b':', 1)
    if stamp_checksum.decode() != join_stamps(stamps):
        redis_client.unlink(key)
        return None

    return data


@handle_connection_failure
def _get_or_lock(key, cond_dnfs, prefix):
    _lock = redis_client.register_script("""
        local locked = redis.call('set', KEYS[1], 'LOCK', 'nx', 'ex', ARGV[1])
        if locked then
            redis.call('del', KEYS[2])
        end
        return locked
    """)
    signal_key = key + ':signal'

    while True:
        data = _read(key, cond_dnfs, prefix)
        if data is None:
            if _lock(keys=[key, signal_key], args=[LOCK_TIMEOUT]):
                return None
        elif data != b'LOCK':
            return data

        # No data and not locked, wait
        redis_client.brpoplpush(signal_key, signal_key, timeout=LOCK_TIMEOUT)


@handle_connection_failure
def _release_lock(key):
    _unlock = redis_client.register_script("""
        if redis.call('get', KEYS[1]) == 'LOCK' then
            redis.call('del', KEYS[1])
        end
        redis.call('lpush', KEYS[2], 1)
        redis.call('expire', KEYS[2], 1)
    """)
    signal_key = key + ':signal'
    _unlock(keys=[key, signal_key])


# Key manipulation helpers

def join_stamps(stamps):
    return hashlib.sha1(b' '.join(stamps)).hexdigest()


def dnfs_to_conj_keys(prefix, cond_dnfs):
    def _conj_cache_key(table, conj):
        conj_str = '&'.join(f'{field}={val}' for field, val in sorted(conj.items()))
        return f'{prefix}conj:{table}:{conj_str}'

    return [_conj_cache_key(table, conj) for table, disj in cond_dnfs.items()
                                         for conj in disj]

def dnfs_to_schemes(cond_dnfs):
    return {table: [",".join(sorted(conj)) for conj in disj]
            for table, disj in cond_dnfs.items() if disj}

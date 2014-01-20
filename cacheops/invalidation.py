# -*- coding: utf-8 -*-
import contextlib
import time
import six
from cacheops.conf import redis_client, handle_connection_failure, USE_SOFT_LOCK, SOFT_LOCK_TIMEOUT
from cacheops.utils import get_model_name, non_proxy


__all__ = ('invalidate_obj', 'invalidate_model', 'invalidate_all')


def serialize_scheme(scheme):
    return ','.join(scheme)

def deserialize_scheme(scheme):
    return tuple(scheme.split(','))

def conj_cache_key(model, conj):
    return 'conj:%s:' % get_model_name(model) + '&'.join('%s=%s' % t for t in sorted(conj))

def conj_cache_key_from_scheme(model, scheme, obj):
    return 'conj:%s:' % get_model_name(model) \
         + '&'.join('%s=%s' % (f, getattr(obj, f)) for f in scheme)

def conj_lock_key(model):
    return 'conj:%s:lock' % get_model_name(model)


class ConjSchemes(object):
    """
    A container for managing models scheme collections.
    Schemes are stored in redis and cached locally.
    """
    def __init__(self):
        self.local = {}
        self.versions = {}

    def get_lookup_key(self, model_or_name):
        if not isinstance(model_or_name, six.string_types):
            model_or_name = get_model_name(model_or_name)
        return 'schemes:%s' % model_or_name

    def get_version_key(self, model_or_name):
        if not isinstance(model_or_name, six.string_types):
            model_or_name = get_model_name(model_or_name)
        return 'schemes:%s:version' % model_or_name

    def get_schemes_lock_key(self, model_or_name):
        if not isinstance(model_or_name, six.string_types):
            model_or_name = get_model_name(model_or_name)
        return "schemes:%s:lock" % model_or_name

    def load_schemes(self, model):
        model_name = get_model_name(model)

        lock_key = self.get_schemes_lock_key(model_name)
        with atomic_redis_pipeline(lock_key) as txn:
            txn.get(self.get_version_key(model))
            txn.smembers(self.get_lookup_key(model_name))
            version, members = txn.execute()

        self.local[model_name] = set(map(deserialize_scheme, members))
        self.local[model_name].add(()) # Всегда добавляем пустую схему
        self.versions[model_name] = int(version or 0)
        return self.local[model_name]

    def schemes(self, model):
        model_name = get_model_name(model)
        try:
            return self.local[model_name]
        except KeyError:
            return self.load_schemes(model)

    def version(self, model):
        try:
            return self.versions[get_model_name(model)]
        except KeyError:
            return 0

    def ensure_known(self, model, new_schemes):
        """
        Ensure that `new_schemes` are known or know them
        """
        new_schemes = set(new_schemes)
        model_name = get_model_name(model)
        loaded = False

        if model_name not in self.local:
            self.load_schemes(model)
            loaded = True

        schemes = self.local[model_name]
        if new_schemes - schemes:
            if not loaded:
                schemes = self.load_schemes(model)
            if new_schemes - schemes:
                # Write new schemes to redis
                lock_key = self.get_schemes_lock_key(model_name)
                with atomic_redis_pipeline(lock_key) as txn:
                    txn.incr(self.get_version_key(model_name))
                    lookup_key = self.get_lookup_key(model_name)
                    for scheme in new_schemes - schemes:
                        txn.sadd(lookup_key, serialize_scheme(scheme))
                        txn.execute()

                # Updating local version
                self.local[model_name].update(new_schemes)
                # We increment here instead of using incr result from redis,
                # because even our updated collection could be already obsolete
                self.versions[model_name] += 1

    def clear(self, model):
        """
        Clears schemes for models
        """
        redis_client.delete(self.get_lookup_key(model))
        redis_client.incr(self.get_version_key(model))

    def clear_all(self):
        self.local = {}
        for model_name in self.versions:
            self.versions[model_name] += 1


cache_schemes = ConjSchemes()


class LockWaitTimeout(Exception):
    """ Error raised if soft lock could not be acquired for limited time.
    """


@contextlib.contextmanager
def atomic_redis_pipeline(lock_key, timeout_ms=SOFT_LOCK_TIMEOUT,
                          redis_client=redis_client):
    """ Context manager which provide atomic Redis pipeline.

    There are two different atomic strategies:
        - Redis native transaction by default
        - Setting lock key if CACHEOPS_USE_SOFT_LOCK = True

    May raise LockWaitTimeout if can`t get lock for 2 * timeout_ms
    """
    try:
        if USE_SOFT_LOCK:
            start_time = time.time()
            while not redis_client.set(lock_key, "1",  nx=True, px=timeout_ms):
                # Something like spin-lock, may be highly CPU expensive.
                if int(time.time() - start_time) * 1000 > 2 * timeout_ms:
                    # Prevent infinite loop
                    raise LockWaitTimeout(lock_key)
        # If soft lock is used then transaction is not needed
        yield redis_client.pipeline(transaction=not USE_SOFT_LOCK)
    finally:
        if USE_SOFT_LOCK:
            redis_client.delete(lock_key)

@handle_connection_failure
def invalidate_obj(obj):
    """
    Invalidates caches that can possibly be influenced by object
    """
    model = non_proxy(obj.__class__)

    # Loading model schemes from local memory (or from redis)
    schemes = cache_schemes.schemes(model)

    # We hope that our schemes are valid, but if not we will update them and redo invalidation
    # on second pass
    for _ in (1, 2):
        # Create a list of invalidators from list of schemes and values of object fields
        conjs_keys = [conj_cache_key_from_scheme(model, scheme, obj) for scheme in schemes]

        # Reading scheme version, cache_keys and deleting invalidators in
        # a single transaction.
        try:
            lock_key = conj_lock_key(model)
            with atomic_redis_pipeline(lock_key) as pipe:
                # get schemes version to check later that it's not obsolete
                pipe.get(cache_schemes.get_version_key(model))
                # Get a union of all cache keys registered in invalidators
                pipe.sunion(conjs_keys)
                # `conjs_keys` are keys of sets containing `cache_keys` we are going to delete,
                # so we'll remove them too.
                # NOTE: There could be some other invalidators not matched with current object,
                #       which reference cache keys we delete, they will be hanging out for a while.
                pipe.delete(*conjs_keys)

                # NOTE: we delete fetched cache_keys later which makes a tiny possibility that something
                #       will fail in the middle leaving those cache keys hanging without invalidators.
                #       The alternative WATCH-based optimistic locking proved to be pessimistic.
                version, cache_keys, _ = pipe.execute()
        except LockWaitTimeout:
            # Looks no good, but we still can clear cache_keys. Conjs_keys
            # will be cleared later, hope so.
            pipe = redis_client.pipeline(transaction=False)
            pipe.get(cache_schemes.get_version_key(model))
            pipe.sunion(conjs_keys)
            version, cache_keys = pipe.execute()

        if cache_keys:
            redis_client.delete(*cache_keys)

        # OK, we invalidated all conjunctions we had in memory, but model schema
        # may have been changed in redis long time ago. If this happened,
        # schema version will not match and we should load new schemes and redo our thing
        if int(version or 0) != cache_schemes.version(model):
            schemes = cache_schemes.load_schemes(model)
        else:
            break

@handle_connection_failure
def invalidate_model(model):
    """
    Invalidates all caches for given model.
    NOTE: This is a heavy artilery which uses redis KEYS request,
          which could be relatively slow on large datasets.
    """
    conjs_keys = redis_client.keys('conj:%s:*' % get_model_name(model))
    if isinstance(conjs_keys, str):
        conjs_keys = conjs_keys.split()

    if conjs_keys:
        cache_keys = redis_client.sunion(conjs_keys)
        redis_client.delete(*(list(cache_keys) + conjs_keys))

    # BUG: a race bug here, ignoring since invalidate_model() is not for hot production use
    cache_schemes.clear(model)

def invalidate_all():
    redis_client.flushdb()
    cache_schemes.clear_all()

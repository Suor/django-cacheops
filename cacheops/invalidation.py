# -*- coding: utf-8 -*-
from redis.exceptions import WatchError

from cacheops.conf import redis_conn
from cacheops.utils import get_model_name


__all__ = ('invalidate_obj', 'invalidate_model')


def serialize_scheme(scheme):
    return ','.join(scheme)

def deserialize_scheme(scheme):
    return tuple(scheme.split(','))

def conj_cache_key(model, conj):
    return 'conj:%s:' % get_model_name(model) + '&'.join('%s=%s' % t for t in sorted(conj))

def conj_cache_key_from_scheme(model, scheme, values):
    return 'conj:%s:' % get_model_name(model) + '&'.join('%s=%s' % (f, values[f]) for f in scheme)


class ConjSchemes(object):
    """
    A container for managing models scheme collections.
    Schemes are stored in redis and cached locally.
    """
    def __init__(self):
        self.local = {}
        self.versions = {}

    def get_lookup_key(self, model_or_name):
        if not isinstance(model_or_name, str):
            model_or_name = get_model_name(model_or_name)
        return 'schemes:%s' % model_or_name

    def get_version_key(self, model_or_name):
        if not isinstance(model_or_name, str):
            model_or_name = get_model_name(model_or_name)
        return 'schemes:%s:version' % model_or_name

    def load_schemes(self, model):
        model_name = get_model_name(model)

        txn = redis_conn.pipeline()
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
                txn = redis_conn.pipeline()
                txn.incr(self.get_version_key(model_name)) # Увеличиваем версию схем

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
        redis_conn.delete(self.get_lookup_key(model))
        redis_conn.incr(self.get_version_key(model))

cache_schemes = ConjSchemes()


def invalidate_from_dict(model, values):
    """
    Invalidates caches that can possibly be influenced by object
    """
    # Create a list of invalidators from list of schemes and values of object fields
    schemes = cache_schemes.schemes(model)
    conjs_keys = [conj_cache_key_from_scheme(model, scheme, values) for scheme in schemes]

    # Get a union of all cache keys registered in invalidators
    # Get schemes version at the same time, hoping it's unchanged
    version_key = cache_schemes.get_version_key(model)
    pipe = redis_conn.pipeline(transaction=False)
    # Optimistic locking: we hope schemes and invalidators won't change while we remove them
    # Ignoring this could lead to cache key hanging with it's invalidator removed
    # HACK: fix for strange WATCH handling in redis-py 2.4.6+
    if hasattr(pipe, 'pipeline_execute_command'):
        pipe.pipeline_execute_command('watch', version_key, *conjs_keys)
    else:
        pipe.watch(version_key, *conjs_keys)
    pipe.get(version_key)
    pipe.sunion(conjs_keys)
    _, version, cache_keys = pipe.execute()

    # Check if our version if schemes for model is obsolete, update them and redo if needed
    # This shouldn't be happen too often once schemes are filled a bit
    if version is None:
        version = 0
    if int(version) != cache_schemes.version(model):
        redis_conn.unwatch()
        cache_schemes.load_schemes(model)
        invalidate_from_dict(model, values)

    elif cache_keys or conjs_keys:
        # `conjs_keys` are keys of sets containing `cache_keys` we are going to delete,
        # so we'll remove them too.
        # NOTE: There could be some other invalidators not matched with current object,
        #       which reference cache keys we delete, they will be hanging out for a while.
        try:
            txn = redis_conn.pipeline()
            txn.delete(*(list(cache_keys) + conjs_keys))
            txn.execute()
        except WatchError:
            # Optimistic locking failed: schemes or one of invalidator sets changed.
            # Just redo everything.
            # NOTE: Scipting will be a cool alternative to optimistic locking
            invalidate_from_dict(model, values)

    else:
        redis_conn.unwatch()


def invalidate_obj(obj):
    """
    Invalidates caches that can possibly be influenced by object
    """
    invalidate_from_dict(obj.__class__, obj.__dict__)


def invalidate_model(model):
    """
    Invalidates all caches for given model.
    NOTE: This is a heavy artilery which uses redis KEYS request, 
          which could be relatively slow on large datasets.
    """
    conjs_keys = redis_conn.keys('conj:%s:*' % get_model_name(model))
    if isinstance(conjs_keys, str):
        conjs_keys = conjs_keys.split()

    if conjs_keys:
        cache_keys = redis_conn.sunion(conjs_keys)
        redis_conn.delete(*(list(cache_keys) + conjs_keys))

    cache_schemes.clear(model)

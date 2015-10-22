import json
import os.path
import re
from threading import local

from funcy import decorator, identity, memoize

try:
    from collections import ChainMap
except ImportError:
    from chainmap import ChainMap

from redis import ConnectionError, TimeoutError
from redis.client import StrictRedis

from .cross import pickle
from .conf import LRU, DEGRADE_ON_FAILURE, REDIS_CONF
import warnings

_marker = object()

STRIP_RE = re.compile(r'TOSTRIP.*/TOSTRIP', re.S)

# Support DEGRADE_ON_FAILURE
if DEGRADE_ON_FAILURE:
    @decorator
    def handle_connection_failure(call):
        try:
            return call()
        except ConnectionError as e:
            warnings.warn("The cacheops cache is unreachable! Error: %s" % e, RuntimeWarning)
        except TimeoutError as e:
            warnings.warn("The cacheops cache timed out! Error: %s" % e, RuntimeWarning)
else:
    handle_connection_failure = identity


class LocalCachedTransactionRedis(StrictRedis):
    def __init__(self, *args, **kwargs):
        super(LocalCachedTransactionRedis, self).__init__(*args, **kwargs)
        self._local = local()

    @memoize
    def load_script(self, name, strip=False):
        """ LUA Script Loader """
        # TODO: strip comments
        filename = os.path.join(os.path.dirname(__file__), 'lua/%s.lua' % name)
        with open(filename) as f:
            code = f.read()
        if strip:
            code = STRIP_RE.sub('', code)
        return self.register_script(code)

    def get(self, name):
        # try transaction local cache first
        try:
            cache_data = self._local.cacheops_transaction_cache.get(name, None)
        except AttributeError:
            # not in transaction
            pass
        else:
            if cache_data is not None and cache_data.get('data', _marker) is not _marker:
                return cache_data['data']
        return super(LocalCachedTransactionRedis, self).get(name)

    @handle_connection_failure
    def cache_thing(self, cache_key, data, cond_dnfs, timeout):
        """
        Writes data to cache and creates appropriate invalidators.
        """
        try:
            # are we in a transaction?
            self._local.cacheops_transaction_cache[cache_key] = {
                'data': pickle.dumps(data, -1),
                'cond_dnfs': cond_dnfs,
                'timeout': timeout,
                # these two help us out later for possible invalidation
                'db_tables': [x for x, y in cond_dnfs],
                'cond_dicts': [dict(i) for x, y in cond_dnfs for i in y]
            }
            return
        except AttributeError:
            # we are not in a transaction.
            pass
        self.load_script('cache_thing', LRU)(
            keys=[cache_key],
            args=[
                pickle.dumps(data, -1),
                json.dumps(cond_dnfs, default=str),
                timeout
            ]
        )

    def start_transaction(self):
        self._local.cacheops_transaction_cache = ChainMap()

    @handle_connection_failure
    def commit_transaction(self):
        for key, value in self._local.cacheops_transaction_cache.items():
            self.load_script('cache_thing', LRU)(
                keys=[key],
                args=[
                    value['data'],
                    json.dumps(value['cond_dnfs'], default=str),
                    value['timeout']
                ]
            )

    def end_transaction(self):
        del self._local.cacheops_transaction_cache

    def start_savepoint(self):
        self._local.cacheops_transaction_cache.maps.append({})

    def commit_savepoint(self):
        self._local.cacheops_transaction_cache.maps[1].update(
            self._local.cacheops_transaction_cache.maps[0]
        )

    def end_savepoint(self):
        self._local.cacheops_transaction_cache.maps.pop(0)

    @handle_connection_failure
    def invalidate_dict(self, db_table, obj_dict):
        try:
            local_cache = self._local.cacheops_transaction_cache
        except AttributeError:
            # not in a transaction
            pass
        else:
            # is this thing in our local cache?
            for key, value in local_cache.items():
                if 'db_tables' in value and 'cond_dicts' in value:
                    for table, cond_dict in zip(value['db_tables'], value['cond_dicts']):
                        # is this key for the table we are invalidating?
                        if table == db_table:
                            match = False
                            for obj_key in set(obj_dict.keys()) & set(cond_dict.keys()):
                                if obj_dict[obj_key] != cond_dict[obj_key]:
                                    break
                            else:
                                match = True
                            if match or not cond_dict:
                                # deep delete, to deal with savepoints in the cache
                                for mapping in local_cache.maps:
                                    if key in mapping:
                                        del mapping[key]

        self.load_script('invalidate')(args=[
            db_table,
            json.dumps(obj_dict, default=str)
        ])

    @handle_connection_failure
    def invalidate_model(self, db_table):
        try:
            local_cache = self._local.cacheops_transaction_cache
        except AttributeError:
            # we are not in a transaction
            pass
        else:
            # remove the same keys from our local cache
            for key, value in local_cache.items():
                if db_table in value.get('db_tables', []):
                    # deep delete, to deal with savepoints in the cache
                    for mapping in local_cache.maps:
                        if key in mapping:
                            del mapping[key]

        conjs_keys = redis_client.keys('conj:%s:*' % db_table)
        if conjs_keys:
            cache_keys = redis_client.sunion(conjs_keys)
            redis_client.delete(*(list(cache_keys) + conjs_keys))

    @handle_connection_failure
    def invalidate_all(self):
        try:
            local_cache = self._local.cacheops_transaction_cache
        except AttributeError:
            # we are not in a transaction
            pass
        else:
            # wipe out our local cache, leaving the same amount of dicts as we found
            local_cache.maps = [{} for x in local_cache.maps]

        self.flushdb()

class SafeRedis(LocalCachedTransactionRedis):
    get = handle_connection_failure(LocalCachedTransactionRedis.get)


class LazyRedis(object):
    def _setup(self):
        # Connecting to redis
        client = (SafeRedis if DEGRADE_ON_FAILURE else LocalCachedTransactionRedis)(**REDIS_CONF)

        object.__setattr__(self, '__class__', client.__class__)
        object.__setattr__(self, '__dict__', client.__dict__)

    def __getattr__(self, name):
        self._setup()
        return getattr(self, name)

    def __setattr__(self, name, value):
        self._setup()
        return setattr(self, name, value)

redis_client = LazyRedis()

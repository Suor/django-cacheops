import json
from operator import itemgetter
import os.path
import re
from threading import local

from funcy import decorator, identity, memoize
import six

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


class CacheMiss(Exception):
    pass


def _find_latest_context(contexts):
    if not contexts:
        return contexts
    latest = contexts[-1]
    if isinstance(latest, list):
        return_value = _find_latest_context(latest)
        if return_value is not None:
            return return_value
        return latest
    return None


def find_latest_context_list(contexts):
    return_value = _find_latest_context(contexts)
    if return_value is None:
        return contexts
    return return_value


def _drop_latest_context(contexts):
    if not contexts:
        return True
    latest = contexts[-1]
    if isinstance(latest, list):
        return_value = _drop_latest_context(latest)
        if return_value:
            contexts.pop()
            return False
        return return_value
    return True


def drop_latest_context(contexts):
    return_value = _drop_latest_context(contexts)
    if return_value:
        raise ValueError('Trying to unshift the top most context.')


def flatten_contexts(contexts):
    for item in contexts:
        if isinstance(item, list):
            # todo: use yield from in python 3
            for x in flatten_contexts(item):
                yield x
        else:
            yield item

cachegetter = itemgetter('cache')


class LocalCachedTransactionRedis(StrictRedis):
    def __init__(self, *args, **kwargs):
        super(LocalCachedTransactionRedis, self).__init__(*args, **kwargs)
        self._local = local()

    def __repr__(self):
        return '<LocalCachedTransactionReids : %r : %r>' % (
            getattr(self._local, 'cacheops_transaction_cache', None),
            getattr(self._local, 'cacheops_invalidation_queue', None)
        )

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
            all_contexts = self._local.cacheops_transaction_contexts
        except AttributeError:
            # not in transaction
            pass
        else:
            # ChainMap looks left to right, our latest contexts are on the right.
            cache_data = ChainMap(
                *map(cachegetter, flatten_contexts(reversed(all_contexts)))
            ).get(name, None)
            if cache_data is not None and cache_data.get('data', _marker) is not _marker:
                return cache_data['data']
        cache_data = super(LocalCachedTransactionRedis, self).get(name)
        if cache_data is None:
            raise CacheMiss
        return pickle.loads(cache_data)

    @handle_connection_failure
    def cache_thing(self, cache_key, data, cond_dnfs, timeout):
        """
        Writes data to cache and creates appropriate invalidators.
        """
        try:
            # are we in a transaction?
            contexts = self._local.cacheops_transaction_contexts
        except AttributeError:
            # we are not in a transaction.
            self.load_script('cache_thing', LRU)(
                keys=[cache_key],
                args=[
                    pickle.dumps(data, -1),
                    json.dumps(cond_dnfs, default=str),
                    timeout
                ]
            )
        else:
            context = find_latest_context_list(contexts)[-1]
            context['cache'][cache_key] = {
                'data': data,
                'cond_dnfs': cond_dnfs,
                'timeout': timeout,
                # these two help us out later for possible invalidation
                'db_tables': [x for x, y in cond_dnfs],
                'cond_dicts': [dict(i) for x, y in cond_dnfs for i in y]
            }

    def start_transaction(self):
        self._local.cacheops_transaction_contexts = [{
            'cache': {},
            'invalidation': []
        }]

    @handle_connection_failure
    def commit_transaction(self):
        # todo: apply all invalidation to caches previous to them ...
        contexts = self._local.cacheops_transaction_contexts
        # del now so attribute errors in invalidate_* and cache_thing methods skip local
        del self._local.cacheops_transaction_contexts

        for context in flatten_contexts(contexts):
            # local caches already have invalidation applied, so do our queued invalidators first
            for item in context['invalidation']:
                if item['type'] == 'dict':
                    self.invalidate_dict(**{x: y for x, y in six.iteritems(item) if x != 'type'})
                elif item['type'] == 'model':
                    self.invalidate_model(**{x: y for x, y in six.iteritems(item) if x != 'type'})
                elif item['type'] == 'all':
                    self.invalidate_all()
            for cache_key, value in six.iteritems(context['cache']):
                self.cache_thing(cache_key, **{x: y for x, y in six.iteritems(value) if x in (
                    'data',
                    'cond_dnfs',
                    'timeout'
                )})

    def rollback_transaction(self):
        del self._local.cacheops_transaction_contexts

    def start_savepoint(self):
        find_latest_context_list(self._local.cacheops_transaction_contexts).append([{
            'cache': {},  # cache
            'invalidation': []  # invalidation
        }])

    def commit_savepoint(self):
        # todo: apply invalidators to outer context
        # todo: should cache get mashed together?
        pass

    def rollback_savepoint(self):
        drop_latest_context(self._local.cacheops_transaction_contexts)

    @handle_connection_failure
    def invalidate_dict(self, db_table, obj_dict):
        try:
            all_contexts = self._local.cacheops_transaction_contexts
        except AttributeError:
            # not in a transaction
            self.load_script('invalidate')(args=[
                db_table,
                json.dumps(obj_dict, default=str)
            ])
        else:
            context = find_latest_context_list(all_contexts)[-1]
            context['invalidation'].append({
                'type': 'dict',
                'db_table': db_table,
                'obj_dict': obj_dict
            })
            # todo: optimize previous context invalidators here?
            # is this thing in our local cache?
            obj_dict_keyset = set(obj_dict.keys())
            cache = context['cache']
            for key, value in list(cache.items()):
                for table, cond_dict in six.izip(value['db_tables'], value['cond_dicts']):
                    if table == db_table:
                        match = False
                        # check equality of any shared keys in obj_dict and cond_dict
                        for obj_key in obj_dict_keyset & set(cond_dict.keys()):
                            if obj_dict[obj_key] != cond_dict[obj_key]:
                                break
                        else:
                            match = True
                        if match or not cond_dict:
                            cache.pop(key)

    @handle_connection_failure
    def invalidate_model(self, db_table):
        try:
            all_contexts = self._local.cacheops_transaction_contexts
        except AttributeError:
            # we are not in a transaction
            conjs_keys = redis_client.keys('conj:%s:*' % db_table)
            if conjs_keys:
                cache_keys = redis_client.sunion(conjs_keys)
                redis_client.delete(*(list(cache_keys) + conjs_keys))
        else:
            context = find_latest_context_list(all_contexts)[-1]
            context['invalidation'].append({
                'type': 'model',
                'db_table': db_table
            })
            # todo: optimize previous context invalidators here?
            # remove the same keys from our local context
            cache = context['cache']
            for key, value in list(cache.items()):
                if db_table in value.get('db_tables', []):
                    cache.pop(key)

    @handle_connection_failure
    def invalidate_all(self):
        try:
            all_contexts = self._local.cacheops_transaction_contexts
        except AttributeError:
            # we are not in a transaction
            self.flushdb()
        else:
            context = find_latest_context_list(all_contexts)[-1]
            context['invalidation'] = [{
                'type': 'all'
            }]  # no previous invalidators matter.
            context['cache'].clear()


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

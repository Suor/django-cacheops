import json
from operator import itemgetter
import itertools
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


def _find_second_latest_context(contexts):
    if not contexts:
        return contexts
    latest = contexts[-1]
    if isinstance(latest, list):
        return_value = _find_second_latest_context(latest)
        if return_value is _marker:
            return latest
        if return_value is not None:
            return return_value
        return _marker
    return None


def find_second_latest_context_list(contexts):
    return_value = _find_second_latest_context(contexts)
    if return_value is None or return_value is _marker:
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

cache_getter = itemgetter('cache')


class InvalidatedData(Exception):
    pass


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

    def get(self, name, local_only=None):
        # try transaction local cache first
        try:
            all_contexts = self._local.cacheops_transaction_contexts
        except AttributeError:
            # not in transaction
            pass
        else:
            cache_item = _marker
            # check for data in cache newest to oldest, starting at newest
            for context in flatten_contexts(reversed(all_contexts)):
                cache_item = context['cache'].get(name, _marker)
                if cache_item is not _marker:
                    break
            if cache_item is not _marker:
                try:
                    # check for invalidation in cache oldest to newest
                    contexts = flatten_contexts(all_contexts)
                    # starting at where we left of looking for data.
                    for c in contexts:
                        if context is c:
                            break
                    for context in itertools.chain([context], contexts):
                        for db_table, cond_dict in six.moves.zip(
                            cache_item['db_tables'],
                            cache_item['cond_dicts']
                        ):
                            for invalidation in context['invalidation']:
                                inv_type = invalidation['type']
                                if inv_type == 'all':
                                    raise InvalidatedData()
                                inv_table = invalidation['db_table']
                                obj_dict = invalidation['obj_dict']
                                obj_dict_keys = set(obj_dict.keys())
                                if db_table == inv_table:
                                    if inv_type == 'model':
                                        raise InvalidatedData()
                                    elif inv_type == 'dict':
                                        # check equality of shared keys in obj_dict and cond_dict
                                        for obj_key in obj_dict_keys & set(cond_dict.keys()):
                                            if obj_dict[obj_key] != cond_dict[obj_key]:
                                                raise InvalidatedData()
                    return cache_item.get('data')
                except InvalidatedData:
                    pass
        if local_only:
            raise CacheMiss
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
        contexts = self._local.cacheops_transaction_contexts
        # del now so attribute errors in invalidate_* and cache_thing methods skip local
        del self._local.cacheops_transaction_contexts

        # apply all invalidation to caches previous to them ...
        for i, context in enumerate(contexts):
            for item in context['invalidation']:
                for previous_context in contexts[i:]:
                    previous_cache = previous_context['cache']
                    if item['type'] == 'dict':
                        self._local_cache_invalidate_dict(
                            previous_cache, **{x: y for x, y in six.iteritems(item) if x != 'type'}
                        )
                    elif item['type'] == 'model':
                        self._local_cache_invalidate_model(
                            previous_cache, **{x: y for x, y in six.iteritems(item) if x != 'type'}
                        )
                    elif item['type'] == 'all':
                        self._local_cache_invalidate_all(previous_cache)
        # todo: optimize redundant invalidation

        # send it out to redis
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
        # apply savepoints invalidation to the outer contexts and add savepoint cache and
        #  invalidation to the outer context list
        outer_contexts = find_second_latest_context_list(self._local.cacheops_transaction_contexts)
        inner_contexts = outer_contexts.pop()
        last_outer_cache = outer_contexts[-1]['cache']
        last_outer_invalidation = outer_contexts[-1]['invalidation']
        for inner_context in inner_contexts:
            for item in inner_context['invalidation']:
                for outer_context in outer_contexts:
                    outer_cache = outer_context['cache']
                    if item['type'] == 'dict':
                        self._local_cache_invalidate_dict(
                            outer_cache, **{x: y for x, y in six.iteritems(item) if x != 'type'}
                        )
                    elif item['type'] == 'model':
                        self._local_cache_invalidate_model(
                            outer_cache, **{x: y for x, y in six.iteritems(item) if x != 'type'}
                        )
                    elif item['type'] == 'all':
                        self._local_cache_invalidate_all(outer_cache)
            last_outer_cache.update(inner_context['cache'])
            last_outer_invalidation.extend(inner_context['invalidation'])
            # todo: optimize redundant invalidation


    def rollback_savepoint(self):
        drop_latest_context(self._local.cacheops_transaction_contexts)

    def _local_cache_invalidate_dict(self, cache, db_table, obj_dict):
        obj_dict_keyset = set(obj_dict.keys())
        for key, value in list(cache.items()):
            for table, cond_dict in six.moves.zip(value['db_tables'], value['cond_dicts']):
                if table == db_table:
                    match = False
                    # check equality of shared keys in obj_dict and cond_dict
                    for obj_key in obj_dict_keyset & set(cond_dict.keys()):
                        if obj_dict[obj_key] != cond_dict[obj_key]:
                            break
                    else:
                        match = True
                    if match or not cond_dict:
                        cache.pop(key)

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
            self._local_cache_invalidate_dict(context['cache'], db_table, obj_dict)

    def _local_cache_invalidate_model(self, cache, db_table):
        for key, value in list(cache.items()):
            if db_table in value.get('db_tables', []):
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
            self._local_cache_invalidate_model(context['cache'], db_table)

    def _local_cache_invalidate_all(self, cache):
        cache.clear()

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
            self._local_cache_invalidate_all(context['cache'])


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

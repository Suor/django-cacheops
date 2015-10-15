# -*- coding: utf-8 -*-
import json
import threading
from funcy import memoize, post_processing, ContextDecorator
from django.db.models.expressions import F
# Since Django 1.8, `ExpressionNode` is `Expression`
try:
    from django.db.models.expressions import ExpressionNode as Expression
except ImportError:
    from django.db.models.expressions import Expression

from .conf import redis_client, handle_connection_failure
from .utils import non_proxy, load_script, NOT_SERIALIZED_FIELDS
from .transaction import Atomic


__all__ = ('invalidate_obj', 'invalidate_model', 'invalidate_all', 'no_invalidation')


@handle_connection_failure
def invalidate_dict(model, obj_dict):
    if no_invalidation.active:
        return
    model = non_proxy(model)
    db_table = model._meta.db_table
    load_script('invalidate')(args=[
        db_table,
        json.dumps(obj_dict, default=str)
    ])

    # is this thing in our local cache?
    try:
        local_cache = Atomic.thread_local.cacheops_transaction_cache
    except AttributeError:
        pass
    else:
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
                        break


def invalidate_obj(obj):
    """
    Invalidates caches that can possibly be influenced by object
    """
    model = non_proxy(obj.__class__)
    invalidate_dict(model, get_obj_dict(model, obj))

@handle_connection_failure
def invalidate_model(model):
    """
    Invalidates all caches for given model.
    NOTE: This is a heavy artilery which uses redis KEYS request,
          which could be relatively slow on large datasets.
    """
    if no_invalidation.active:
        return
    model = non_proxy(model)
    db_table = model._meta.db_table
    conjs_keys = redis_client.keys('conj:%s:*' % db_table)
    if conjs_keys:
        cache_keys = redis_client.sunion(conjs_keys)
        redis_client.delete(*(list(cache_keys) + conjs_keys))

    # remove the same keys from our local cache, if we are in a transaction
    try:
        local_cache = Atomic.thread_local.cacheops_transaction_cache
    except AttributeError:
        pass
    else:
        for key, value in local_cache.items():
            if db_table in value.get('db_tables', []):
                # deep delete, to deal with savepoints in the cache
                for mapping in local_cache.maps:
                    if key in mapping:
                        del mapping[key]

@handle_connection_failure
def invalidate_all():
    if no_invalidation.active:
        return
    redis_client.flushdb()

    # wipe out our local cache, if we are in a transaction
    try:
        # leave the same amount of dicts as we found, but empty them
        Atomic.thread_local.cacheops_transaction_cache.maps = [
            {} for x in Atomic.thread_local.cacheops_transaction_cache.maps
        ]
    except AttributeError:
        pass


class InvalidationState(threading.local):
    def __init__(self):
        self.depth = 0

class _no_invalidation(ContextDecorator):
    state = InvalidationState()

    def __enter__(self):
        self.state.depth += 1

    def __exit__(self, type, value, traceback):
        self.state.depth -= 1

    @property
    def active(self):
        return self.state.depth

no_invalidation = _no_invalidation()


### ORM instance serialization

@memoize
def serializable_fields(model):
    return tuple(f for f in model._meta.fields
                   if not isinstance(f, NOT_SERIALIZED_FIELDS))

@post_processing(dict)
def get_obj_dict(model, obj):
    for field in serializable_fields(model):
        value = getattr(obj, field.attname)
        if value is None:
            yield field.attname, None
        elif isinstance(value, (F, Expression)):
            continue
        else:
            yield field.attname, field.get_prep_value(value)

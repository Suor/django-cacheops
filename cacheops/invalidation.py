# -*- coding: utf-8 -*-
import json
import threading
from funcy import memoize, post_processing, ContextDecorator
from django.db import DEFAULT_DB_ALIAS
from django.db.models.expressions import F, Expression

from .conf import settings
from .utils import NOT_SERIALIZED_FIELDS
from .sharding import get_prefix
from .redis import redis_client, handle_connection_failure, load_script
from .signals import cache_invalidated
from .transaction import queue_when_in_transaction


__all__ = ('invalidate_obj', 'invalidate_model', 'invalidate_all', 'no_invalidation')


@queue_when_in_transaction
@handle_connection_failure
def invalidate_dict(model, obj_dict, using=DEFAULT_DB_ALIAS):
    if no_invalidation.active or not settings.CACHEOPS_ENABLED:
        return
    model = model._meta.concrete_model
    prefix = get_prefix(_cond_dnfs=[(model._meta.db_table, list(obj_dict.items()))], dbs=[using])
    load_script('invalidate')(keys=[prefix], args=[
        model._meta.db_table,
        json.dumps(obj_dict, default=str)
    ])
    cache_invalidated.send(sender=model, obj_dict=obj_dict)


def invalidate_obj(obj, using=DEFAULT_DB_ALIAS):
    """
    Invalidates caches that can possibly be influenced by object
    """
    model = obj.__class__._meta.concrete_model
    invalidate_dict(model, get_obj_dict(model, obj), using=using)


@queue_when_in_transaction
@handle_connection_failure
def invalidate_model(model, using=DEFAULT_DB_ALIAS):
    """
    Invalidates all caches for given model.
    NOTE: This is a heavy artillery which uses redis KEYS request,
          which could be relatively slow on large datasets.
    """
    if no_invalidation.active or not settings.CACHEOPS_ENABLED:
        return
    model = model._meta.concrete_model
    # NOTE: if we use sharding dependent on DNF then this will fail,
    #       which is ok, since it's hard/impossible to predict all the shards
    prefix = get_prefix(tables=[model._meta.db_table], dbs=[using])
    conjs_keys = redis_client.keys('%sconj:%s:*' % (prefix, model._meta.db_table))
    if conjs_keys:
        cache_keys = redis_client.sunion(conjs_keys)
        redis_client.delete(*(list(cache_keys) + conjs_keys))
    cache_invalidated.send(sender=model, obj_dict=None)


@handle_connection_failure
def invalidate_all():
    if no_invalidation.active or not settings.CACHEOPS_ENABLED:
        return
    redis_client.flushdb()
    cache_invalidated.send(sender=None, obj_dict=None)


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

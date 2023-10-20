import json
import threading
from funcy import memoize, post_processing, ContextDecorator, decorator, walk_values
from django.db import DEFAULT_DB_ALIAS
from django.db.models.expressions import F, Expression

from .conf import settings
from .sharding import get_prefix
from .redis import redis_client, handle_connection_failure, load_script
from .signals import cache_invalidated
from .transaction import queue_when_in_transaction


__all__ = ('invalidate_obj', 'invalidate_model', 'invalidate_all', 'no_invalidation')


@decorator
def skip_on_no_invalidation(call):
    if not settings.CACHEOPS_ENABLED or no_invalidation.active:
        return
    return call()


@skip_on_no_invalidation
@queue_when_in_transaction
@handle_connection_failure
def invalidate_dict(model, obj_dict, using=DEFAULT_DB_ALIAS):
    if no_invalidation.active or not settings.CACHEOPS_ENABLED:
        return

    model = model._meta.concrete_model
    prefix = get_prefix(_cond_dnfs=[(model._meta.db_table, list(obj_dict.items()))], dbs=[using])

    if settings.CACHEOPS_INSIDEOUT:
        script = 'invalidate_insideout'
        serialized_dict = json.dumps(walk_values(str, obj_dict))
    else:
        script = 'invalidate'
        serialized_dict = json.dumps(obj_dict, default=str)
    load_script(script)(keys=[prefix], args=[model._meta.db_table, serialized_dict])
    cache_invalidated.send(sender=model, obj_dict=obj_dict)


@skip_on_no_invalidation
def invalidate_obj(obj, using=DEFAULT_DB_ALIAS):
    """
    Invalidates caches that can possibly be influenced by object
    """
    model = obj.__class__._meta.concrete_model
    invalidate_dict(model, get_obj_dict(model, obj), using=using)


@skip_on_no_invalidation
@queue_when_in_transaction
@handle_connection_failure
def invalidate_model(model, using=DEFAULT_DB_ALIAS):
    """
    Invalidates all caches for given model.
    NOTE: This is a heavy artillery which uses redis KEYS request,
          which could be relatively slow on large datasets.
    """
    model = model._meta.concrete_model
    # NOTE: if we use sharding dependent on DNF then this will fail,
    #       which is ok, since it's hard/impossible to predict all the shards
    prefix = get_prefix(tables=[model._meta.db_table], dbs=[using])
    conjs_keys = redis_client.keys('%sconj:%s:*' % (prefix, model._meta.db_table))
    if conjs_keys:
        if settings.CACHEOPS_INSIDEOUT:
            redis_client.unlink(*conjs_keys)
        else:
            cache_keys = redis_client.sunion(conjs_keys)
            keys = list(cache_keys) + conjs_keys
            redis_client.unlink(*keys)
    cache_invalidated.send(sender=model, obj_dict=None)


@skip_on_no_invalidation
@handle_connection_failure
def invalidate_all():
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
    return {f for f in model._meta.fields
              if f.get_internal_type() not in settings.CACHEOPS_SKIP_FIELDS}

@post_processing(dict)
def get_obj_dict(model, obj):
    for field in serializable_fields(model):
        # Skip deferred fields, in post_delete trying to fetch them results in error anyway.
        # In post_save we rely on deferred values be the same as in pre_save.
        if field.attname not in obj.__dict__:
            continue

        value = getattr(obj, field.attname)
        if value is None:
            yield field.attname, None
        elif isinstance(value, (F, Expression)):
            continue
        else:
            yield field.attname, field.get_prep_value(value)

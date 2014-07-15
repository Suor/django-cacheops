# -*- coding: utf-8 -*-
from funcy import memoize
from .cross import json

from .conf import redis_client, handle_connection_failure
from .utils import non_proxy, load_script, NOT_SERIALIZED_FIELDS


__all__ = ('invalidate_obj', 'invalidate_model', 'invalidate_all')


@handle_connection_failure
def invalidate_dict(model, obj_dict):
    model = non_proxy(model)
    load_script('invalidate')(args=[
        model._meta.db_table,
        json.dumps(obj_dict, default=str)
    ])

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
    model = non_proxy(model)
    conjs_keys = redis_client.keys('conj:%s:*' % model._meta.db_table)
    if conjs_keys:
        cache_keys = redis_client.sunion(conjs_keys)
        redis_client.delete(*(list(cache_keys) + conjs_keys))

@handle_connection_failure
def invalidate_all():
    redis_client.flushdb()


### ORM instance serialization

@memoize
def serializable_fields(model):
    return tuple(f for f in model._meta.fields
                   if not isinstance(f, NOT_SERIALIZED_FIELDS))

def serialize_value(field, value):
    if value is None:
        return value
    else:
        return field.get_prep_value(value)

def get_obj_dict(model, obj):
    return dict(
        (field.attname, serialize_value(field, getattr(obj, field.attname)))
        for field in serializable_fields(model)
    )

# -*- coding: utf-8 -*-
import simplejson as json

from cacheops.conf import redis_client, handle_connection_failure
from cacheops.funcy import memoize
from cacheops.utils import get_model_name, non_proxy, load_script, NON_SERIALIZABLE_FIELDS


__all__ = ('invalidate_obj', 'invalidate_model', 'invalidate_all')


@handle_connection_failure
def invalidate_obj(obj):
    """
    Invalidates caches that can possibly be influenced by object
    """
    model = non_proxy(obj.__class__)
    load_script('invalidate')(args=[
        get_model_name(model),
        serialize_object(model, obj)
    ])

@handle_connection_failure
def invalidate_model(model):
    """
    Invalidates all caches for given model.
    NOTE: This is a heavy artilery which uses redis KEYS request,
          which could be relatively slow on large datasets.
    """
    model = non_proxy(model)
    conjs_keys = redis_client.keys('conj:%s:*' % get_model_name(model))
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
                   if not isinstance(f, NON_SERIALIZABLE_FIELDS))

def serialize_object(model, obj):
    obj_dict = dict(
        (field.attname, field.get_prep_value(getattr(obj, field.attname)))
        for field in serializable_fields(model)
    )
    return json.dumps(obj_dict, default=str)

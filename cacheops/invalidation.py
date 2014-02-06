# -*- coding: utf-8 -*-
import simplejson as json
from django.db import models

from cacheops.conf import redis_client, handle_connection_failure
from cacheops.utils import get_model_name, non_proxy, load_script


__all__ = ('invalidate_obj', 'invalidate_model', 'invalidate_all')


invalidate_script = load_script('invalidate')


@handle_connection_failure
def invalidate_obj(obj):
    """
    Invalidates caches that can possibly be influenced by object
    """
    model = non_proxy(obj.__class__)
    invalidate_script(args=[
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
    conjs_keys = redis_client.keys('conj:%s:*' % get_model_name(model))
    if conjs_keys:
        cache_keys = redis_client.sunion(conjs_keys)
        redis_client.delete(*(list(cache_keys) + conjs_keys))

def invalidate_all():
    redis_client.flushdb()


### ORM instance serialization

NON_SERIALIZABLE_FIELDS = (
    models.FileField,
    models.TextField, # One should not filter by long text equality
)
if hasattr(models, 'BinaryField'):
    NON_SERIALIZABLE_FIELDS += (models.BinaryField,) # Not possible to filter by it

def serializable_attnames(model, cache={}):
    if model not in cache:
        cache[model] = tuple(f.attname for f in model._meta.fields
                             if not isinstance(f, NON_SERIALIZABLE_FIELDS))
    return cache[model]

def serialize_object(model, obj):
    return json.dumps(dict((f, getattr(obj, f)) for f in serializable_attnames(model)), default=str)

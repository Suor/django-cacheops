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

from .redis_client import redis_client, handle_connection_failure
from .utils import non_proxy, NOT_SERIALIZED_FIELDS

__all__ = ('invalidate_obj', 'invalidate_model', 'invalidate_all', 'no_invalidation')


def invalidate_dict(model, obj_dict):
    if no_invalidation.active:
        return
    model = non_proxy(model)
    db_table = model._meta.db_table
    redis_client.invalidate_dict(db_table, obj_dict)

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
    redis_client.invalidate_model(db_table)

@handle_connection_failure
def invalidate_all():
    if no_invalidation.active:
        return
    redis_client.invalidate_all()


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

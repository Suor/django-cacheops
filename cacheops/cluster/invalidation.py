import json
import threading
import re
from funcy import memoize, post_processing, ContextDecorator, decorator
from django.db import DEFAULT_DB_ALIAS
from django.db.models.expressions import F, Expression

from cacheops.conf import settings
from cacheops.sharding import get_prefix
from cacheops.redis import redis_client, handle_connection_failure, load_script
from cacheops.signals import cache_invalidated
from cacheops.transaction import queue_when_in_transaction

from cacheops.cluster.key_crafter import craft_scheme_key, craft_invalidator_key


__all__ = ('invalidate_obj', 'invalidate_model', 'invalidate_all', 'no_invalidation')


@decorator
def skip_on_no_invalidation(call):
    if not settings.CACHEOPS_ENABLED or no_invalidation.active:
        return

    if not settings.CACHEOPS_CLUSTER_ENABLED:
        return

    return call()

# ================== lua script functions ==================

def craft_conj_dict(scheme, old_data):
    conj = {}

    for field in re.split(r'[,]+', scheme):
        field_value = old_data.get(field)

        conj[field] = field_value

    return conj


def calculate_conj_key(prefix, db_table, old_data):
    conj_keys = []
    # return a set of binarty
    schemes = redis_client.smembers(craft_scheme_key(prefix, db_table))
    schemes = map(lambda s: s.decode('utf-8'), schemes)

    for scheme in schemes:
        conj = craft_conj_dict(scheme, old_data)
        conj_keys.append(
            craft_invalidator_key(prefix, db_table, conj)
        )

    return conj_keys


def remove_chunk(client, data_keys):
    for i in range(0, len(data_keys), 500):
        client.delete(*data_keys[i: i + 500])


def invalidation_dict_transaction(prefix, db_table, obj):
    # not a part of transaction, since getting key may take a long time
    # hence, we shouldn't include it

    # step 1: create the pattern to get invalidation keys
    conj_keys = calculate_conj_key(prefix, db_table, obj)
    if not conj_keys:
        return

    # step 2: get all the invalidation keys
    invalidation_keys = conj_keys
    if not invalidation_keys:
        return

    # step 3: get all the data keys from invalidation keys
    # each invalidation keys will hold a list of data keys
    # simple diagram: invalidation-key -> data-key -> data
    # so once we have a invalidation key, we will get all the data key it is holding and invalidate them
    data_keys = list(redis_client.sunion(invalidation_keys))

    # step 4: removal

    # remove all data key and its data
    remove_chunk(client=redis_client, data_keys=data_keys)

    # remove all invalidation keys
    redis_client.delete(*invalidation_keys)

    # end invalidation transactions

# =========================================

@skip_on_no_invalidation
@queue_when_in_transaction
@handle_connection_failure
def invalidate_dict(model, obj_dict, using=DEFAULT_DB_ALIAS):
    """
    This function is patched to remove the LUA Script from being used. Since the Lua script can only run on 1 node
    However, we are using redis cluster for a better scaling -> This lead to some keys are not able to invalidate because of lua script limitation

    To solve this, I converted the lua script into python so that we can use RedisCluster client to perform invalidate action on multiple nodes
    The logic of this function will be as close to the lua script as possible

    Some code from lua script that only work with single node will be modified to work with multiple nodes
    """
    if no_invalidation.active or not settings.CACHEOPS_ENABLED:
        return

    model = model._meta.concrete_model
    prefix = get_prefix(_cond_dnfs=[(model._meta.db_table, list(obj_dict.items()))], dbs=[using])

    # INFO: this part is removed since it only works on single redis cluster node
    # load_script('invalidate')(keys=[prefix], args=[
    #     model._meta.db_table,
    #     json.dumps(obj_dict, default=str)
    # ])

    # move to use redis client = redis cluster client
    # so that we can invalidate all the keys across multiple clusters
    invalidation_dict_transaction(
        prefix=prefix,
        db_table=model._meta.db_table,
        obj=obj_dict,
    )

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
    # !WARNING: performance issue on large dataset due to KEYS command search with pattern
    conjs_keys = redis_client.keys("conj:%s:*" % (model._meta.db_table))
    if conjs_keys:
        cache_keys = redis_client.sunion(conjs_keys)
        keys = list(cache_keys) + conjs_keys
        redis_client.delete(*keys)
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

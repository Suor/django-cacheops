"""
Implementation of cache thing lua script to distribute the key across clusters
"""

from cacheops.redis import redis_client

from cacheops.cluster.key_crafter import craft_scheme_key, craft_invalidator_key

# A pair of funcs
# NOTE: we depend here on keys order being stable
def conj_schema(conj: dict) -> str:
    """
    create a field list that used by filter to save in the scheme keys
    for example: ModelA.objects.filter(x=1, y=2) -> return "x,y"

    the conj object contain field:value pair used by filter function
    """
    field_list = conj.keys() if conj else []
    parts = [str(field) for field in field_list]

    return ','.join(parts)


def cache_thing(keys, args, strip=False):
    """
    The cache_thing script is smart enough to store enough information so that we can craft the invalidator keys from invalidation flow
    Since we have enough information to craft invalidation keys, schemes key. We don't have to put all these keys in 1 node
    -> Move the Lua script content to python so that we can distribute the key to multiple node
    """
    prefix, key = keys
    precall_key, data, query_info, timeout = args

    if precall_key != '' and redis_client.exists(precall_key) == 0:
    # Cached data was invalidated during the function call. The data is
    # stale and should not be cached.
        return

    # Update schemes and invalidators
    for db_table, query_set in query_info.items():
        for conj in query_set:
            # Ensure scheme is known
            redis_client.sadd(
                craft_scheme_key(prefix, db_table),
                conj_schema(conj),
            )

            # Add new cache_key to list of dependencies
            # craft_invalidator_key is the better name for conj_cache_key
            conj_key = craft_invalidator_key(prefix, db_table, conj)
            redis_client.sadd(conj_key, key)
            # NOTE: an invalidator should live longer than any key it references.
            #       So we update its ttl on every key if needed.
            # NOTE: if CACHEOPS_LRU is True when invalidators should be left persistent,
            #       so we strip next section from this script.

            if strip:
                continue

            conj_ttl = redis_client.ttl(conj_key)
            if conj_ttl < timeout:
                # We set conj_key life with a margin over key life to call expire rarer
                # And add few extra seconds to be extra safe
                redis_client.expire(conj_key, timeout * 2 + 10)

    # Write data to cache
    # this should be here because if we unable to set invalidator key for current data key, we will have the outdated value
    # -> no one want to have an outdated value
    # but by write the data to cache after the invalidator key stuff
    # if we are unable to set the data -> we will have another change next time
    # also, in the invalidation process, deleting non-exist key is an accepted behaviour by redis
    has_lock = redis_client.get(key) == b'LOCK'
    if has_lock:
        redis_client.setex(key, timeout, data)
    else:
        redis_client.set(key, data, ex=timeout, nx=True)

import json
from hashlib import md5


def cluster_prefix(query):
    """
    use this function to gen prefix -> add hash tag for redis cluster
    """
    cache_key = None
    if hasattr(query, '_queryset'):
        cache_key = query._queryset._cache_key(False)
    else:
        """
        This part here is used by cacheops to generate md5 hash for a @cached_as decorator
        Basically, this obj_key and func_cache_key will be used to generate md5 hash for result caching key
        I will reuse this one since md5 has a good randomness -> help to distribute the key more evenly through clusters
        """

        # prevent circular import
        from cacheops.utils import obj_key
        factors = []
        try:
            factors = [query.func, query._cond_dnfs, query.dbs]
        except:
            factors = [query.func, query.dbs]

        cache_key = md5(json.dumps(factors, sort_keys=True, default=obj_key).encode('utf-8')).hexdigest()
        cache_key = f"q:{cache_key}"

    cache_key = cache_key[2:10] # only get first 8 digits, to reduce the prefix size

    return f"{{{cache_key}}}"

from redis.client import StrictRedis
from django.db.transaction import Atomic

_marker = object()

class LocalCachedTransactionRedis(StrictRedis):
    def get(self, name):
        # try transaction local cache first
        try:
            cache_data = Atomic.thread_local.cacheops_transaction_cache.get(name, None)
        except AttributeError:
            # not in transaction
            pass
        else:
            if cache_data is not None and cache_data.get('data', _marker) is not _marker:
                return cache_data['data']
        return super(LocalCachedTransactionRedis, self).get(name)

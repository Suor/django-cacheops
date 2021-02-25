from cacheops import invalidation
from cacheops.conf import settings
from cacheops.cluster import invalidation as cluster_invalidation

class Invalidator(object):
    """
    Abstract layer to call correct invalidation function for cluster or normal mode
    """
    def __getattr__(self, name):
        invalidator = cluster_invalidation if settings.CACHEOPS_CLUSTER_ENABLED else invalidation
        res = getattr(invalidator, name)

        # Save to dict to speed up next access, __getattr__ won't be called
        self.__dict__[name] = res
        return res

invalidator = Invalidator()

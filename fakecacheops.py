import redis
from django.db.models import Manager
from django.db.models.query import QuerySet
from django.conf import settings


# Connecting to redis
try:
    redis_conf = settings.CACHEOPS_REDIS
except AttributeError:
    raise ImproperlyConfigured('You must specify non-empty CACHEOPS_REDIS setting to use cacheops')

redis_client = redis.StrictRedis(**redis_conf)


# query
QuerySet._cache_key = lambda self, extra=None: None
Manager.nocache = lambda self: self
Manager.cache = lambda self: self
Manager.inplace = lambda self: self


# invalidation
def invalidate_obj(obj):
    pass

def invalidate_model(model):
    pass


# substitute cacheops
import sys
sys.modules['cacheops'] = sys.modules['fakecacheops']
sys.modules['cacheops.conf'] = sys.modules['fakecacheops']

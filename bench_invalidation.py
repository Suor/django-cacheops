# coding: utf-8

# Синтетика для алгоритмов инвалидации
# - с транзакцией
# - с локами без объекдинения ключей на клиенте (redis, twem)
# - с локами и объединением ключей на клиенте (redis, twem)
import functools
from random import randint
from timeit import timeit
from uuid import uuid4
import sys
import os
import redis

os.environ['DJANGO_SETTINGS_MODULE'] = 'tests.settings'

from cacheops.invalidation import redis_lock_acquire, redis_lock_release


def native(redis_client, conjs_keys):
    """ Оригинальный алгоритм с транзакцией на redis
    """

    # Reading scheme version, cache_keys and deleting invalidators in
    # a single transaction.
    def _invalidate_conjs(pipe):
        # get schemes version to check later that it's not obsolete
        # pipe.get(cache_schemes.get_version_key(model))
        # Get a union of all cache keys registered in invalidators
        pipe.sunion(conjs_keys)
        # `conjs_keys` are keys of sets containing `cache_keys` we are going to delete,
        # so we'll remove them too.
        # NOTE: There could be some other invalidators not matched with current object,
        #       which reference cache keys we delete, they will be hanging out for a while.
        pipe.delete(*conjs_keys)

    # NOTE: we delete fetched cache_keys later which makes a tiny possibility that something
    #       will fail in the middle leaving those cache keys hanging without invalidators.
    #       The alternative WATCH-based optimistic locking proved to be pessimistic.
    cache_keys, _ = redis_client.transaction(_invalidate_conjs)
    if cache_keys:
        redis_client.delete(*cache_keys)


def twem_proxy_compat(redis_client, conjs_keys):
    """ Алгоритм на программных локах, совместимый с twem proxy - базовая
    версия.
    """
    for ck in conjs_keys:
        lock_key = redis_lock_acquire("lock:" + ck, redis_client=redis_client)
        pipe = redis_client.pipeline(transaction=False)
        pipe.smembers(ck)
        pipe.delete(ck)
        cache_keys, _ = pipe.execute()
        redis_lock_release(lock_key, redis_client=redis_client)
        if cache_keys:
            redis_client.delete(*cache_keys)

def twem_proxy_compat_opt_1(redis_client, conjs_keys):
    """ Совместимый с twem proxy - все операции за 2 pipeline.
    """
    # Тут придётся лочить все ключи модели
    lock_key = redis_lock_acquire("lock:model_name", redis_client=redis_client)
    pipe = redis_client.pipeline(transaction=False)
    for ck in conjs_keys:
        pipe.smembers(ck)
        pipe.delete(ck)
    redis_lock_release(lock_key, redis_client=redis_client)

    results = pipe.execute()
    pipe = redis_client.pipeline(transaction=False)
    for cache_keys in results[::2]:
        if cache_keys:
            pipe.delete(*cache_keys)
    pipe.execute()


def twem_proxy_compat_opt_2(redis_client, conjs_keys):
    """ Совместимый с twem proxy - слияние всех cache key на клиенте.
    """
    lock_key = redis_lock_acquire("lock:model_name", redis_client=redis_client)
    pipe = redis_client.pipeline(transaction=False)
    for ck in conjs_keys:
        pipe.smembers(ck)
        pipe.delete(ck)
    results = pipe.execute()
    redis_lock_release(lock_key, redis_client=redis_client)

    s = set()
    for cache_keys in results[::2]:
        if cache_keys:
            s |= cache_keys
    redis_client.delete(*cache_keys)


def twem_proxy_compat_opt_3(redis_client, conjs_keys):
    """ Совместимый с twem proxy - допускаем что все conj_keys на одном
    сервере и возвращаем sunion.

    Допущение возможно за счёт настройки twemproxy - hash_tag
    """
    lock_key = redis_lock_acquire("lock:model_name", redis_client=redis_client)
    pipe = redis_client.pipeline(transaction=False)
    pipe.sunion(*conjs_keys)
    pipe.delete(*conjs_keys)
    cache_keys, _ = pipe.execute()
    redis_lock_release(lock_key, redis_client=redis_client)

    if cache_keys:
        redis_client.delete(*cache_keys)


def generate_conjs_keys_map(cache_keys=100, conjs_keys=100):
    conjs_keys_map = {'conj:model_name:' + uuid4().hex:
                      [randint(1, 100) for j in xrange(1, cache_keys)]
                      for i in xrange(1, conjs_keys)}

    return conjs_keys_map


def populate_conjs_keys(redis_client, conjs_keys_map):
    conjs_keys = []
    pipe = redis_client.pipeline(transaction=False)
    for conjs_key, cache_keys in conjs_keys_map.items():
        for k in cache_keys:
            pipe.set(k, 1)
        pipe.sadd(conjs_key, *cache_keys)
        conjs_keys.append(conjs_key)
    pipe.execute()
    return conjs_keys



REDIS_CONF = {
    'host': '192.168.144.151',
    'port': 6379,
    'db': 0,
    'socket_timeout': 60}

TWEM_CONF = {
    'host': '192.168.144.170',
    'port': 6379,
    'socket_timeout': 60}


def run_bench():

    redis_client = redis.StrictRedis(**REDIS_CONF)
    twem_client = redis.StrictRedis(**TWEM_CONF)

    cases = ((10, 10), (100, 100), (100, 1000), (1000, 100))

    for conjs_keys_num, cache_keys_num in cases:

        print "\nConj keys: {}, cache keys: {}\n".format(conjs_keys_num,
                                                         cache_keys_num)

        conjs_keys_map = generate_conjs_keys_map(conjs_keys_num, cache_keys_num)

        funcs = (native, twem_proxy_compat, twem_proxy_compat_opt_1,
                 twem_proxy_compat_opt_2, twem_proxy_compat_opt_3)
        for f in funcs:
            for name, cli in (('Redis', redis_client), ('TWEM', twem_client)):
                try:
                    print "Backend: {} - {}".format(name, f.__name__)
                    if name == 'TWEM' and f.__name__ == 'native':
                        raise RuntimeError('unsupported')
                    conjs_keys = populate_conjs_keys(cli, conjs_keys_map)
                    stmt = functools.partial(f, cli, conjs_keys)
                    res = timeit(stmt, number=1)
                    print "\t time: {}".format(res)
                except Exception as e:
                    print "\t error: {}".format(e)
                    #raise

if __name__ == '__main__':
    run_bench()
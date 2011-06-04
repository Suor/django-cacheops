# -*- coding: utf-8 -*-
from redis.exceptions import WatchError

from cacheops.conf import redis_conn
from cacheops.utils import get_model_name


__all__ = ('invalidate_obj', 'invalidate_model', 'clear_model_schemes')


def serialize_scheme(scheme):
    return ','.join(scheme)

def deserialize_scheme(scheme):
    return tuple(scheme.split(','))

def conj_cache_key(model, conj):
    return 'conj:%s:' % get_model_name(model) + '&'.join('%s=%s' % t for t in sorted(conj))

def conj_cache_key_from_scheme(model, scheme, values):
    return 'conj:%s:' % get_model_name(model) + '&'.join('%s=%s' % (f, values[f]) for f in scheme)


class ConjSchemes(object):
    """
    Объект хранящий и управляющий схемами конъюнкций для каждой модели.
    Схемы хранятся локально и подтягиваются при необходимости, запрашивать не бить редис каждый раз.
    """
    def __init__(self):
        self.local = {}
        self.versions = {}

    def get_lookup_key(self, model_or_name):
        if not isinstance(model_or_name, str):
            model_or_name = get_model_name(model_or_name)
        return 'schemes:%s' % model_or_name

    def get_version_key(self, model_or_name):
        if not isinstance(model_or_name, str):
            model_or_name = get_model_name(model_or_name)
        return 'schemes:%s:version' % model_or_name

    def load_schemes(self, model):
        model_name = get_model_name(model)

        txn = redis_conn.pipeline()
        txn.get(self.get_version_key(model))
        txn.smembers(self.get_lookup_key(model_name))
        version, members = txn.execute()

        self.local[model_name] = set(map(deserialize_scheme, members))
        self.local[model_name].add(()) # Всегда добавляем пустую схему
        self.versions[model_name] = int(version or 0)
        return self.local[model_name]

    def schemes(self, model):
        model_name = get_model_name(model)
        try:
            return self.local[model_name]
        except KeyError:
            return self.load_schemes(model)

    def version(self, model):
        try:
            return self.versions[get_model_name(model)]
        except KeyError:
            return 0

    def ensure_known(self, model, new_schemes):
        """
        Убеждается, что схемы нам известны. Если нет регистрирует их и добавляет в редис.
        """
        new_schemes = set(new_schemes)
        model_name = get_model_name(model)
        loaded = False

        if model_name not in self.local:
            self.load_schemes(model)
            loaded = True
        schemes = self.local[model_name]

        if new_schemes - schemes:
            if not loaded:
                schemes = self.load_schemes(model)
            if new_schemes - schemes:
                # Пишем новые схемы в редис
                txn = redis_conn.pipeline()
                txn.incr(self.get_version_key(model_name)) # Увеличиваем версию схем

                lookup_key = self.get_lookup_key(model_name)
                for scheme in new_schemes - schemes:
                    txn.sadd(lookup_key, serialize_scheme(scheme))
                txn.execute()

                # Обновляем локальную копию и версию
                self.local[model_name].update(new_schemes)
                self.versions[model_name] += 1 # здесь добавляем 1, а не ставим что получили от incr,
                                               # т.к. даже наша новая версия может быть уже устаревшей

cache_schemes = ConjSchemes()


def invalidate_from_dict(model, values):
    """
    Инвалидация кеша для объекта модели с переданным словарём данных
    """
    schemes = cache_schemes.schemes(model)
    conjs_keys = [conj_cache_key_from_scheme(model, scheme, values) for scheme in schemes]

    # Выберем все запросы подлежащие инвалидации, как объединение запросов всех инвалидаторов
    # Заодно выберем версию схем, чтобы подтянуть новые если надо
    version_key = cache_schemes.get_version_key(model)
    pipe = redis_conn.pipeline(transaction=False)
    pipe.watch(version_key, *conjs_keys)
    pipe.get(version_key)
    pipe.sunion(conjs_keys)
    _, version, queries = pipe.execute()

    # Если версия схем устарела, то список запросов для инвалидации может быть неполным и всё нужно переделать
    # Такое будет случаться всё реже по мере заполнения схем
    if version is not None and int(version) != cache_schemes.version(model):
        redis_conn.unwatch()
        cache_schemes.load_schemes(model)
        invalidate_from_dict(model, values)

    elif queries or conjs_keys:
        # conjs_keys указывают на инвалидируемые запросы, так что они больше не понадобятся
        # Вообще могут быть другие конъюнкции не затронутые текущим объектом,
        # но указывающие на инвалидируемые запросы, они останутся висеть какое-то время
        try:
            txn = redis_conn.pipeline()
            txn.delete(*(list(queries) + conjs_keys))
            txn.execute()
        except WatchError:
            # пока мы тут крутились множества соотв. conjs_keys могли пополниться
            # или версия схем увеличиться
            # в таком случае наш список queries устарел - делаем всё заново
            invalidate_from_dict(model, values)

    else:
        redis_conn.unwatch()


def invalidate_obj(obj):
    """
    Инвалидируем все запросы, которые затронуты объектом.
    """
    invalidate_from_dict(obj.__class__, obj.__dict__)


def invalidate_model(model):
    """
    Инвалидируем все запросы модели.
    Тяжёлая артилерия, использует релисовый KEYS и поэтому относительно медленна.
    """
    conjs_keys = redis_conn.keys('conj:%s:*' % get_model_name(model))
    if isinstance(conjs_keys, str):
        conjs_keys = conjs_keys.split()

    if conjs_keys:
        queries = redis_conn.sunion(conjs_keys)
        redis_conn.delete(*(list(queries) + conjs_keys))


def clear_model_schemes(model):
    redis_conn.delete(cache_schemes.get_lookup_key(model), cache_schemes.get_version_key(model))

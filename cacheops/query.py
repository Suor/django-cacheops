# -*- coding: utf-8 -*-
try:
    import cPickle as pickle
except ImportError:
    import pickle
from functools import wraps

from django.core.exceptions import ImproperlyConfigured
from django.db.models import Manager
from django.db.models.query import QuerySet, ValuesQuerySet, ValuesListQuerySet, DateQuerySet
from django.db.models.signals import post_save, post_delete, m2m_changed
from django.utils.hashcompat import md5_constructor

from cacheops.conf import model_profile, redis_conn
from cacheops.utils import monkey_mix, dnf, conj_scheme, get_model_name
from cacheops.invalidation import cache_schemes, conj_cache_key, invalidate_obj, invalidate_model


__all__ = ('cacheoped_method', 'cacheoped_as', 'install_cacheops')

_old_objs = {}
_local_get_cache = {}


def cache_thing(model, cache_key, data, cond_dnf=[[]], timeout=None):
    """
    Кэширует переданные данные и прописывает требуемые инвалидаторы.
    """
    if timeout is None:
        profile = model_profile(model)
        timeout = profile['timeout']

    # Познаём новые схемы
    schemes = map(conj_scheme, cond_dnf)
    cache_schemes.ensure_known(model, schemes)

    txn = redis_conn.pipeline()

    # Пишем в кеш
    pickled_data = pickle.dumps(data, -1)
    if timeout is not None:
        txn.setex(cache_key, pickled_data, timeout)
    else:
        txn.set(cache_key, pickled_data)

    # Добавляем ссылки на текущую выборку для всех конъюнкций
    for conj in cond_dnf:
        conj_key = conj_cache_key(model, conj)
        txn.sadd(conj_key, cache_key)
        if timeout is not None:
            # Таймаут инвалидатора должен быть больше таймаута его элементов
            # Поэтому берём его из профиля, он максимальный
            # Добавляем 10 секунд на всякий случай, мало ли как там редис их чистит
            txn.expire(conj_key, model._cacheprofile['timeout'] + 10)

    txn.execute()


def cacheoped_method(action='fetch', extra=None):
    # TODO: избавиться от декоратора, метод может использовать локальную функцию с @cacheoped_as
    def decorator(func):
        key_extra = extra if extra is not None else '%s.%s' % (func.__module__, func.__name__)

        @wraps(func)
        def wrapper(self, *args, **kwargs):
            cache_this = self._cacheprofile is not None and action in self._cacheops
            if cache_this:
                cache_key = self._cache_key(extra=key_extra)
                cache_data = redis_conn.get(cache_key)
                if cache_data is not None:
                    return pickle.loads(cache_data)

            result = func(self, *args, **kwargs)
            if cache_this:
                self._cache_results(cache_key, result)
            return result

        return wrapper
    return decorator


def cacheoped_as(queryset, extra=None, timeout=None):
    """
    Кэширует результаты функции и инвалидирует как переданный queryset.
    NOTE: Кэширует всегда игнорируя профиль queryset-а
    TODO: Можно оптимизировать для того случая когда передаём простой queryset
          вроде Category.objects.all(), чтобы не ренденрить sql-запрос и не считать ДНФ
    """
    queryset._require_cacheprofile()
    if timeout and timeout > queryset._cacheprofile['timeout']:
        raise NotImplementedError('timeout override should be smaller than default')

    def decorator(func):
        if extra:
            key_extra = extra
        else:
            key_extra = '%s.%s' % (func.__module__, func.__name__)
        cache_key = queryset._cache_key(extra=key_extra)
        cond_dnf = dnf(queryset.query.where, queryset.model._meta.db_table)

        @wraps(func)
        def wrapper(*args):
            # NOTE: вообще предполагается, что аргументов не будет,
            #       в том случае если они есть, то результат не должен от них зависеть
            cache_data = redis_conn.get(cache_key)
            if cache_data is not None:
                return pickle.loads(cache_data)

            result = func(*args)
            cache_thing(queryset.model, cache_key, result, cond_dnf, timeout or queryset._cachetimeout)
            return result

        return wrapper
    return decorator


def _stringify_query():
    import simplejson as json
    from datetime import datetime, date
    from django.db.models.fields import Field
    from django.db.models.sql.where import Constraint, WhereNode, ExtraWhere
    from django.db.models.sql import Query
    from django.db.models.sql.aggregates import Aggregate
    from django.db.models.sql.datastructures import RawValue, Date
    from django.db.models.sql.expressions import SQLEvaluator

    attrs = {}
    attrs[WhereNode] = ('connector', 'negated', 'children', 'subtree_parents')
    attrs[ExtraWhere] = ('sqls', 'params')
    attrs[Aggregate] = ('source', 'is_summary', 'col', 'extra')
    attrs[RawValue] = ('value')
    attrs[Date] = ('col', 'lookup_type')

    q = Query(None)
    q_keys = q.__dict__.keys()
    q_ignored = ['join_map', 'dupe_avoidance', '_extra_select_cache', '_aggregate_select_cache']
    attrs[Query] = tuple(sorted( set(q_keys) - set(q_ignored) ))

    for k, v in attrs.items():
        attrs[k] = map(intern, v)

    def encode_object(obj):
        if isinstance(obj, set):
            return sorted(obj)
        elif isinstance(obj, type):
            return '%s.%s' % (obj.__module__, obj.__name__)
        elif isinstance(obj, (datetime, date)):
            return str(obj)
        elif isinstance(obj, Constraint):
            return (obj.alias, obj.col)
        elif isinstance(obj, Field):
            return (obj.model, obj.name)
        elif isinstance(obj, QuerySet):
            return (obj.__class__, obj.query)
        elif obj.__class__ in attrs:
            return (obj.__class__, [getattr(obj, attr) for attr in attrs[obj.__class__]])
        elif isinstance(obj, Aggregate):
            return (obj.__class__, [getattr(obj, attr) for attr in attrs[Aggregate]])
        elif isinstance(obj, Query):
            # for custom subclasses
            return (obj.__class__, [getattr(obj, attr) for attr in attrs[Query]])
        elif isinstance(obj, SQLEvaluator):
            return (obj.__class__, obj.expression.__dict__.items())
        else:
            raise TypeError("Can't encode %s" % repr(obj))

    def stringify_query(query):
        return json.dumps(query, default=encode_object, skipkeys=True, sort_keys=True, separators=(',',':'))

    return stringify_query
stringify_query = _stringify_query()


class QuerySetMixin(object):
    def __init__(self, *args, **kwargs):
        self._no_monkey.__init__(self, *args, **kwargs)
        self._cloning = 1000
        if not hasattr(self, '_cacheprofile') and self.model:
            self._cacheprofile = model_profile(self.model)
            self._cache_write_only = False
            if self._cacheprofile is not None:
                self._cacheops = self._cacheprofile['ops']
                self._cachetimeout = self._cacheprofile['timeout']
            else:
                self._cacheops = None
                self._cachetimeout = None

    def get_or_create(self, **kwargs):
        """
        Отключаем кеш для get_or_create
        """
        return self.nocache()._no_monkey.get_or_create(self, **kwargs)

    def _require_cacheprofile(self):
        if self._cacheprofile is None:
            raise ImproperlyConfigured('Cacheops is not enabled for %s model.\n'
                                       'If you don\'t want to cache anything by default you can "just_enable" it.'
                                        % get_model_name(self.model))

    def _cache_key(self, extra=''):
        """
        Ключ для кеширования результатов этого запроса
        """
        md5 = md5_constructor()
        md5.update(str(self.__class__))
        md5.update(stringify_query(self.query))
        if extra:
            md5.update(str(extra))
        # Атрибут flat влияет на выдачу результатов для ValuesQuerySet
        if hasattr(self, 'flat'):
            md5.update(str(self.flat))

        return 'q:%s' % md5.hexdigest()

    def _cache_results(self, cache_key, results):
        # Познаём новые схемы
        cond_dnf = dnf(self.query.where, self.model._meta.db_table)
        cache_thing(self.model, cache_key, results, cond_dnf, timeout=self._cachetimeout)

    def cache(self, ops=None, timeout=None, clone=False, write_only=None):
        """
        Возвращает копию queryset-а с включенным кэшированием.
        По-умолчанию включаются все доступные операции.
        """
        self._require_cacheprofile()
        if timeout and timeout > self._cacheprofile['timeout']:
            raise NotImplementedError('timeout override should be smaller than default')

        if ops is None and timeout is None:
            ops = ['get', 'fetch', 'count']
        if isinstance(ops, str):
            ops = [ops]
        qs = self._clone() if clone else self

        if ops is not None:
            qs._cacheops = set(ops)
        if timeout is not None:
            qs._cachetimeout = timeout
        if write_only is not None:
            qs._cache_write_only = write_only
        return qs

    def nocache(self, clone=False):
        """
        Метод для удобства, выключает кэширование для queryset-а.
        """
        # Если профиль не настроен, то и выключать ничего не надо
        if self._cacheprofile is None:
            return self.clone() if clone else self
        else:
            return self.cache(ops=[], clone=clone)

    def cloning(self, cloning=1000):
        self._cloning = cloning
        return self

    def inplace(self):
        return self.cloning(0)

    def _clone(self, klass=None, setup=False, **kwargs):
        if self._cloning or klass is not None:
            return self.clone(klass, setup, **kwargs)
        else:
            self.__dict__.update(kwargs)
            return self

    def clone(self, klass=None, setup=False, **kwargs):
        kwargs.setdefault('_cacheprofile', self._cacheprofile)
        kwargs.setdefault('_cacheops', self._cacheops)
        kwargs.setdefault('_cachetimeout', self._cachetimeout)
        kwargs.setdefault('_cache_write_only', self._cache_write_only)

        clone = self._no_monkey._clone(self, klass, setup, **kwargs)
        clone._cloning = self._cloning - 1 if self._cloning else 0
        return clone

    def iterator(self):
        superiter = self._no_monkey.iterator
        cache_this = self._cacheprofile is not None and 'fetch' in self._cacheops

        if cache_this:
            cache_key = self._cache_key()
            if not self._cache_write_only:
                # Пытаемся загрузить из кеша
                cache_data = redis_conn.get(cache_key)
                if cache_data is not None:
                    results = pickle.loads(cache_data)
                    for obj in results:
                        yield obj
                    raise StopIteration

        results = []
        for obj in superiter(self):
            if cache_this:
                results.append(obj)
            yield obj

        if cache_this:
            self._cache_results(cache_key, results)
        raise StopIteration

    def count(self):
        # Если внутренний кеш queryset-а уже заполнен, то просто посчитаем его
        if self._result_cache is not None and not self._iter:
            return len(self._result_cache)
        return cacheoped_method(action='count', extra='count')(lambda self: self.query.get_count(using=self.db))(self)

    def get(self, *args, **kwargs):
        # Т.к. далее вызовется обычный итератор, который не будет знать, что его вызвали из get()
        # то здесь нужно сделать клонирование с назначением кэширования операции fetch
        # NOTE: можно ещё из iterator() смотреть назад по фреймам, но это гораздо больший пиздец
        if self._cacheprofile is not None and 'get' in self._cacheops:
            # Используем local_get если так настроено
            # Не связываемся с Q-объектами, родственниками и фильтрованными кверисетами
            if self._cacheprofile['local_get']    \
                and not args                      \
                and not self.query.select_related \
                and not self.query.where.children:

                key = (self.__class__, self.model) + tuple(sorted(kwargs.items()))
                try:
                    return _local_get_cache[key]
                except KeyError:
                    _local_get_cache[key] = self._no_monkey.get(self, *args, **kwargs)
                    return _local_get_cache[key]
            elif 'fetch' in self._cacheops:
                qs = self
            else:
                qs = self._clone().cache()
        else:
            qs = self

        return qs._no_monkey.get(qs, *args, **kwargs)

    def exists(self):
        """
        Переписываем этот метод, чтобы при выборке по pk он сохранял полученный объект в локальное хранилище.
        Нужно, чтобы в обработке post_save использовать старые данные.
        """
        # TODO: отрефакторить эту функцию
        if self._cacheprofile is not None:
            query_dnf = dnf(self.query.where, self.model._meta.db_table)
            if len(query_dnf) == 1 and len(query_dnf[0]) == 1 and query_dnf[0][0][0] == self.model._meta.pk.name:
                result = len(self.nocache()) > 0
                if result:
                    _old_objs[get_model_name(self.model)][query_dnf[0][0][1]] = self._result_cache[0]
                return result
        return self._no_monkey.exists(self)


class ManagerMixin(object):
    def contribute_to_class(self, cls, name):
        self._no_monkey.contribute_to_class(self, cls, name)
        cls._cacheprofile = model_profile(cls)
        if cls._cacheprofile is not None and get_model_name(cls) not in _old_objs:
            # Устанавливаем сигналы
            post_save.connect(self._post_save, sender=cls)
            post_delete.connect(self._post_delete, sender=cls)
            #post_update.connect(self.post_update, sender=cls)
            _old_objs[get_model_name(cls)] = {}

    def _post_save(self, sender, instance, **kwargs):
        """
        Инвалидирует и для старой и для новой версии объекта, старый объект подтягивается
        когда django делает exists перед апдейтом.
        """
        old = _old_objs[get_model_name(instance.__class__)].pop(instance.pk, None)
        if old:
            invalidate_obj(old)
        invalidate_obj(instance)

        # Включенный cache_on_save заполняет кеш сразу после сохранения,
        # используя сохраняемый объект.
        cache_on_save = instance._cacheprofile.get('cache_on_save')
        if cache_on_save:
            # В кеш ложится объект, созданный где-то раньше, а не только что выбранный из базы,
            # как это бывает при обычном кешировании. Объект сериализуется целиком, поэтому
            # может быть достигнут нежелательный эффект: сохранено некоторое состояние объекта на момент сохранения.
            # В частности, ForeignKey и его потомки кешируют выбранные связанные объекты в
            # атрибутах вида _FIELDNAME_cache. Чтобы не сохранять связанные объекты,
            # перед кешированием нужно изъять все атрибуты вида _FIELDNAME_cache из экземпляра,
            # а после сохранения — вернуть на место.

            # Изымаем нежелательные атрибуты
            unwanted_attrs = [k for k in instance.__dict__.keys() if k.startswith('_') and k.endswith('_cache')]
            unwanted_dict = dict((k, instance.__dict__[k]) for k in unwanted_attrs)
            for k in unwanted_attrs:
                del instance.__dict__[k]

            # Кешируем
            key = cache_on_save if isinstance(cache_on_save, basestring) else 'pk'
            # Джанго не позволяет делать запросы типа related_id = 1337,
            # поэтому нужно срезать _id с конца
            filter_key = key[:-3] if key.endswith('_id') else key
            cacheoped_as(instance.__class__.objects \
                .filter(**{filter_key: getattr(instance, key)}), extra='') \
                (lambda: [instance])()

            # Восстанавливаем нежелательные атрибуты
            instance.__dict__.update(unwanted_dict)

    def _post_delete(self, sender, instance, **kwargs):
        # NOTE: сработает некорректно если какой-то засранец менял поля прежде чем удалить объект
        invalidate_obj(instance)

    def inplace(self):
        return self.get_query_set().inplace()

    def get(self, *args, **kwargs):
        return self.get_query_set().inplace().get(*args, **kwargs)

    def cache(self, *args, **kwargs):
        return self.get_query_set().cache(*args, **kwargs)

    def nocache(self, *args, **kwargs):
        return self.get_query_set().nocache(*args, **kwargs)


def invalidate_m2m(sender=None, instance=None, model=None, action=None, pk_set=None, **kwargs):
    if action in ('post_add', 'post_remove', 'post_clear'):
        invalidate_model(sender)
        invalidate_obj(instance)
        # TODO: возможно следует инвалидировать и добавляемые/удаляемые модели как-то


def install_cacheops():
    """
    Инсталлирует кешопс путём массовых обезьяних патчей
    """
    monkey_mix(Manager, ManagerMixin)
    monkey_mix(QuerySet, QuerySetMixin)
    monkey_mix(ValuesQuerySet, QuerySetMixin, ['iterator'])
    monkey_mix(ValuesListQuerySet, QuerySetMixin, ['iterator'])
    monkey_mix(DateQuerySet, QuerySetMixin, ['iterator'])

    # В админке кеш выключаем
    from django.contrib.admin.options import ModelAdmin
    def ModelAdmin_queryset(self, request):
        queryset = o_ModelAdmin_queryset(self, request)
        if queryset._cacheprofile is None:
            return queryset
        else:
            return queryset.nocache()
    o_ModelAdmin_queryset = ModelAdmin.queryset
    ModelAdmin.queryset = ModelAdmin_queryset

    # Обработка изменения m2m
    m2m_changed.connect(invalidate_m2m)

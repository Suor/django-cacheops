# -*- coding: utf-8 -*-
import sys
try:
    import cPickle as pickle
except ImportError:
    import pickle
from functools import wraps
import hashlib

import django
from django.core.exceptions import ImproperlyConfigured
from django.db.models import Manager, Model
from django.db.models.query import QuerySet, ValuesQuerySet, ValuesListQuerySet, DateQuerySet
from django.db.models.signals import pre_save, post_save, post_delete, m2m_changed

try:
    from django.db.models.query import MAX_GET_RESULTS
except ImportError:
    MAX_GET_RESULTS = None

from cacheops.conf import model_profile, redis_client, handle_connection_failure
from cacheops.utils import monkey_mix, dnf, conj_scheme, get_model_name, non_proxy, stamp_fields
from cacheops.invalidation import cache_schemes, conj_cache_key, invalidate_obj, invalidate_model


__all__ = ('cached_method', 'cached_as', 'install_cacheops')

_old_objs = {}
_local_get_cache = {}


@handle_connection_failure
def cache_thing(model, cache_key, data, cond_dnf=[[]], timeout=None):
    """
    Writes data to cache and creates appropriate invalidators.
    """
    model = non_proxy(model)

    if timeout is None:
        profile = model_profile(model)
        timeout = profile['timeout']

    # Ensure that all schemes of current query are "known"
    schemes = map(conj_scheme, cond_dnf)
    cache_schemes.ensure_known(model, schemes)

    txn = redis_client.pipeline()

    # Write data to cache
    pickled_data = pickle.dumps(data, -1)
    if timeout is not None:
        txn.setex(cache_key, timeout, pickled_data)
    else:
        txn.set(cache_key, pickled_data)

    # Add new cache_key to list of dependencies for every conjunction in dnf
    for conj in cond_dnf:
        conj_key = conj_cache_key(model, conj)
        txn.sadd(conj_key, cache_key)
        if timeout is not None:
            # Invalidator timeout should be larger than timeout of any key it references
            # So we take timeout from profile which is our upper limit
            # Add few extra seconds to be extra safe
            txn.expire(conj_key, model._cacheprofile['timeout'] + 10)

    txn.execute()


def cached_as(sample, extra=None, timeout=None):
    """
    Caches results of a function and invalidates them same way as given queryset.
    NOTE: Ignores queryset cached ops settings, just caches.
    """
    # If we unexpectedly get list instead of queryset return identity decorator.
    # Paginator could do this when page.object_list is empty.
    # TODO: think of better way doing this.
    if isinstance(sample, (list, tuple)):
        return lambda func: func
    elif isinstance(sample, Model):
        queryset = sample.__class__.objects.inplace().filter(pk=sample.pk)
    elif isinstance(sample, type) and issubclass(sample, Model):
        queryset = sample.objects.all()
    else:
        queryset = sample

    queryset._require_cacheprofile()
    if timeout and timeout > queryset._cacheprofile['timeout']:
        raise NotImplementedError('timeout override should be smaller than default')

    def decorator(func):
        if extra:
            key_extra = extra
        else:
            key_extra = '%s.%s' % (func.__module__, func.__name__)
        cache_key = queryset._cache_key(extra=key_extra)

        @wraps(func)
        def wrapper(*args):
            # NOTE: These args must not effect function result.
            #       I'm keeping them to cache view functions.
            cache_data = redis_client.get(cache_key)
            if cache_data is not None:
                return pickle.loads(cache_data)

            result = func(*args)
            queryset._cache_results(cache_key, result, timeout)
            return result

        return wrapper
    return decorator


def cached_method(op='fetch', extra=None):
    def decorator(method):
        @wraps(method)
        def wrapper(self, *args, **kwargs):
            func = method
            if self._cacheprofile is not None and op in self._cacheops:
                func = cached_as(self, extra=extra or op)(func)
            return func(self, *args, **kwargs)
        return wrapper
    return decorator


def _stringify_query():
    """
    Serializes query object, so that it can be used to create cache key.
    We can't just do pickle because order of keys in dicts is arbitrary,
    we can use str(query) which compiles it to SQL, but it's too slow,
    so we use json.dumps with sort_keys=True and object hooks.

    NOTE: I like this function no more than you, it's messy
          and pretty hard linked to django internals.
          I just don't have nicer solution for now.

          Probably the best way out of it is optimizing SQL generation,
          which would be valuable by itself. The problem with it is that
          any significant optimization will most likely require a major
          refactor of sql.Query class, which is a substantial part of ORM.
    """
    import simplejson as json
    from datetime import datetime, date
    from django.db.models.expressions import ExpressionNode, F
    from django.db.models.fields import Field
    from django.db.models.fields.related import ManyToOneRel, OneToOneRel
    from django.db.models.sql.where import Constraint, WhereNode, ExtraWhere, \
                                           EverythingNode, NothingNode
    from django.db.models.sql import Query
    from django.db.models.sql.aggregates import Aggregate
    from django.db.models.sql.datastructures import Date
    from django.db.models.sql.expressions import SQLEvaluator

    attrs = {}

    # A new things in Django 1.6
    try:
        from django.db.models.sql.where import EmptyWhere, SubqueryConstraint
        attrs[EmptyWhere] = ()
        attrs[SubqueryConstraint] = ('alias', 'columns', 'targets', 'query_object')
    except ImportError:
        pass

    # RawValue removed in Django 1.7
    try:
        from django.db.models.sql.datastructures import RawValue
        attrs[RawValue] = ('value',)
    except ImportError:
        pass

    attrs[WhereNode] = attrs[ExpressionNode] \
        = ('connector', 'negated', 'children')
    attrs[SQLEvaluator] = ('expression',)
    attrs[ExtraWhere] = ('sqls', 'params')
    attrs[Aggregate] = ('source', 'is_summary', 'col', 'extra')
    attrs[Date] = ('col', 'lookup_type')
    attrs[F] = ('name',)
    attrs[ManyToOneRel] = attrs[OneToOneRel] = ('field',)
    attrs[EverythingNode] = attrs[NothingNode] = ()

    q = Query(None)
    q_keys = q.__dict__.keys()
    q_ignored = ['join_map', 'dupe_avoidance', '_extra_select_cache', '_aggregate_select_cache',
                 'used_aliases']
    attrs[Query] = tuple(sorted( set(q_keys) - set(q_ignored) ))

    for k, v in attrs.items():
        attrs[k] = map(intern, v)

    def encode_object(obj):
        if isinstance(obj, set):
            return sorted(obj)
        elif isinstance(obj, type):
            return '%s.%s' % (obj.__module__, obj.__name__)
        elif hasattr(obj, '__uniq_key__'):
            return (obj.__class__, obj.__uniq_key__())
        elif isinstance(obj, (datetime, date)):
            return str(obj)
        elif isinstance(obj, Constraint):
            return (obj.alias, obj.col)
        elif isinstance(obj, Field):
            return (obj.model, obj.name)
        elif obj.__class__ in attrs:
            return (obj.__class__, [getattr(obj, attr) for attr in attrs[obj.__class__]])
        elif isinstance(obj, QuerySet):
            return (obj.__class__, obj.query)
        elif isinstance(obj, Aggregate):
            return (obj.__class__, [getattr(obj, attr) for attr in attrs[Aggregate]])
        elif isinstance(obj, Query):
            # for custom subclasses of Query
            return (obj.__class__, [getattr(obj, attr) for attr in attrs[Query]])
        else:
            raise TypeError("Can't stringify %s" % repr(obj))

    def stringify_query(query):
        # HACK: Catch TypeError and reraise it as ValueError
        #       since django hides it and behave weird when gets a TypeError in Queryset.iterator()
        try:
            return json.dumps(query, default=encode_object, skipkeys=True,
                                     sort_keys=True, separators=(',',':'))
        except TypeError as e:
            raise ValueError(*e.args)

    return stringify_query
stringify_query = _stringify_query()


class QuerySetMixin(object):
    def __init__(self, *args, **kwargs):
        self._no_monkey.__init__(self, *args, **kwargs)
        self._cloning = 1000
        self._cacheprofile = None
        if self.model:
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
        Disabling cache for get or create
        TODO: check whether we can use cache (or write_only) here without causing problems
        """
        return self.nocache()._no_monkey.get_or_create(self, **kwargs)

    def _require_cacheprofile(self):
        if self._cacheprofile is None:
            raise ImproperlyConfigured(
                'Cacheops is not enabled for %s model.\n'
                'If you don\'t want to cache anything by default you can "just_enable" it.'
                    % get_model_name(self.model))

    def _cache_key(self, extra=''):
        """
        Compute a cache key for this queryset
        """
        md5 = hashlib.md5()
        md5.update(str(self.__class__))
        md5.update(stamp_fields(self.model)) # Protect from field list changes in model
        md5.update(stringify_query(self.query))
        # If query results differ depending on database
        if not self._cacheprofile['db_agnostic']:
            md5.update(self.db)
        if extra:
            md5.update(str(extra))
        # 'flat' attribute changes results formatting for ValuesQuerySet
        if hasattr(self, 'flat'):
            md5.update(str(self.flat))

        return 'q:%s' % md5.hexdigest()

    def _cache_results(self, cache_key, results, timeout=None):
        cond_dnf = dnf(self)
        cache_thing(self.model, cache_key, results, cond_dnf, timeout or self._cachetimeout)

    def cache(self, ops=None, timeout=None, write_only=None):
        """
        Enables caching for given ops
            ops        - a subset of ['get', 'fetch', 'count'],
                         ops caching to be turned on, all enabled by default
            timeout    - override default cache timeout
            write_only - don't try fetching from cache, still write result there

        NOTE: you actually can disable caching by omiting corresponding ops,
              .cache(ops=[]) disables caching for this queryset.
        """
        self._require_cacheprofile()
        if timeout and timeout > self._cacheprofile['timeout']:
            raise NotImplementedError('timeout override should be smaller than default')

        if ops is None and timeout is None:
            ops = ['get', 'fetch', 'count']
        if isinstance(ops, str):
            ops = [ops]

        if ops is not None:
            self._cacheops = set(ops)
        if timeout is not None:
            self._cachetimeout = timeout
        if write_only is not None:
            self._cache_write_only = write_only
        return self

    def nocache(self, clone=False):
        """
        Convinience method, turns off caching for this queryset
        """
        # cache profile not present means caching is not enabled for this model
        if self._cacheprofile is None:
            return self
        else:
            return self.cache(ops=[])

    def cloning(self, cloning=1000):
        self._cloning = cloning
        return self

    def inplace(self):
        return self.cloning(0)

    def _clone(self, klass=None, setup=False, **kwargs):
        if self._cloning:
            return self.clone(klass, setup, **kwargs)
        elif klass is not None:
            # HACK: monkey patch self.query.clone for single call
            #       to return itself instead of cloning
            original_query_clone = self.query.clone
            def query_clone():
                self.query.clone = original_query_clone
                return self.query
            self.query.clone = query_clone
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
        # TODO: do not cache empty queries in Django 1.6
        superiter = self._no_monkey.iterator
        cache_this = self._cacheprofile and 'fetch' in self._cacheops

        if cache_this:
            cache_key = self._cache_key()
            if not self._cache_write_only:
                # Trying get data from cache
                cache_data = redis_client.get(cache_key)
                if cache_data is not None:
                    results = pickle.loads(cache_data)
                    for obj in results:
                        yield obj
                    raise StopIteration

        # Cache miss - fallback to overriden implementation
        results = []
        for obj in superiter(self):
            if cache_this:
                results.append(obj)
            yield obj

        if cache_this:
            self._cache_results(cache_key, results)
        raise StopIteration

    def count(self):
        # Optmization borrowed from overriden method:
        # if queryset cache is already filled just return its len
        # NOTE: there is no self._iter in Django 1.6+, so we use getattr() for compatibility
        if self._result_cache is not None and not getattr(self, '_iter', None):
            return len(self._result_cache)
        return cached_method(op='count')(self._no_monkey.count)(self)

    def get(self, *args, **kwargs):
        # .get() uses the same .iterator() method to fetch data,
        # so here we add 'fetch' to ops
        if self._cacheprofile and 'get' in self._cacheops:
            # NOTE: local_get=True enables caching of simple gets in local memory,
            #       which is very fast, but not invalidated.
            # Don't bother with Q-objects, select_related and previous filters,
            # simple gets - thats what we are really up to here.
            if self._cacheprofile['local_get']    \
                and not args                      \
                and not self.query.select_related \
                and not self.query.where.children:
                # NOTE: We use simpler way to generate a cache key to cut costs.
                #       Some day it could produce same key for diffrent requests.
                key = (self.__class__, self.model) + tuple(sorted(kwargs.items()))
                try:
                    return _local_get_cache[key]
                except KeyError:
                    _local_get_cache[key] = self._no_monkey.get(self, *args, **kwargs)
                    return _local_get_cache[key]
                except TypeError:
                    pass # If some arg is unhashable we can't save it to dict key,
                         # we just skip local cache in that case

            if 'fetch' in self._cacheops:
                qs = self
            else:
                qs = self._clone().cache()
        else:
            qs = self

        return qs._no_monkey.get(qs, *args, **kwargs)

    def exists(self):
        """
        HACK: handling invalidation in post_save signal requires both
              old and new object data, to get old data without extra db request
              we use exists() call from django's Model.save_base().
              Yes, if you use .exists() yourself this can cause memory leak.
        """
        # TODO: refactor this one to more understandable something
        if self._cacheprofile:
            query_dnf = dnf(self)
            if len(query_dnf) == 1 and len(query_dnf[0]) == 1 \
               and query_dnf[0][0][0] == self.model._meta.pk.name:
                result = len(self.nocache()) > 0
                if result:
                    _old_objs[self.model][query_dnf[0][0][1]] = self._result_cache[0]
                return result
        return self._no_monkey.exists(self)


class ManagerMixin(object):
    def _install_cacheops(self, cls):
        cls._cacheprofile = model_profile(cls)
        if cls._cacheprofile is not None and cls not in _old_objs:
            # Set up signals
            if not getattr(cls._meta, 'select_on_save', True):
                # Django 1.6+ doesn't make select on save by default,
                # which we use to fetch old state, so we fetch it ourselves in pre_save handler
                pre_save.connect(self._pre_save, sender=cls)
            post_save.connect(self._post_save, sender=cls)
            post_delete.connect(self._post_delete, sender=cls)
            _old_objs[cls] = {}

            # Install auto-created models as their module attributes to make them picklable
            module = sys.modules[cls.__module__]
            if not hasattr(module, cls.__name__):
                setattr(module, cls.__name__, cls)

    def contribute_to_class(self, cls, name):
        self._no_monkey.contribute_to_class(self, cls, name)
        self._install_cacheops(cls)

    def _pre_save(self, sender, instance, **kwargs):
        try:
            _old_objs[sender][instance.pk] = sender.objects.get(pk=instance.pk)
        except sender.DoesNotExist:
            pass

    def _post_save(self, sender, instance, **kwargs):
        """
        Invokes invalidations for both old and new versions of saved object
        """
        old = _old_objs[sender].pop(instance.pk, None)
        if old:
            invalidate_obj(old)
        invalidate_obj(instance)

        # Enabled cache_on_save makes us write saved object to cache.
        # Later it can be retrieved with .get(<cache_on_save_field>=<value>)
        # <cache_on_save_field> is pk unless specified.
        # This sweet trick saves a db request and helps with slave lag.
        cache_on_save = instance._cacheprofile.get('cache_on_save')
        if cache_on_save:
            # HACK: We get this object "from field" so it can contain
            #       some undesirable attributes or other objects attached.
            #       RelatedField accessors do that, for example.
            #
            #       So we strip down any _*_cache attrs before saving
            #       and later reassign them
            # Stripping up undesirable attributes
            unwanted_attrs = [k for k in instance.__dict__.keys()
                                if k.startswith('_') and k.endswith('_cache')]
            unwanted_dict = dict((k, instance.__dict__[k]) for k in unwanted_attrs)
            for k in unwanted_attrs:
                del instance.__dict__[k]

            key = cache_on_save if isinstance(cache_on_save, basestring) else 'pk'
            # Django doesn't allow filters like related_id = 1337.
            # So we just hacky strip _id from end of a key
            # TODO: make it right, _meta.get_field() should help
            filter_key = key[:-3] if key.endswith('_id') else key

            cond = {filter_key: getattr(instance, key)}
            qs = sender.objects.inplace().filter(**cond).order_by()
            if MAX_GET_RESULTS:
                qs = qs[:MAX_GET_RESULTS + 1]
            qs._cache_results(qs._cache_key(), [instance])

            # Reverting stripped attributes
            instance.__dict__.update(unwanted_dict)

    def _post_delete(self, sender, instance, **kwargs):
        """
        Invalidation upon object deletion.
        """
        # NOTE: this will behave wrong if someone changed object fields
        #       before deletion (why anyone will do that?)
        invalidate_obj(instance)

    # Django 1.5- compatability
    if django.VERSION < (1, 6):
        get_queryset = Manager.get_query_set

    def inplace(self):
        return self.get_queryset().inplace()

    def get(self, *args, **kwargs):
        return self.get_queryset().inplace().get(*args, **kwargs)

    def cache(self, *args, **kwargs):
        return self.get_queryset().cache(*args, **kwargs)

    def nocache(self, *args, **kwargs):
        return self.get_queryset().nocache(*args, **kwargs)


def invalidate_m2m(sender=None, instance=None, model=None, action=None, pk_set=None, **kwargs):
    """
    Invoke invalidation on m2m changes.
    """
    if action in ('post_add', 'post_remove', 'post_clear'):
        invalidate_model(sender) # NOTE: this is harsh, but what's the alternative?
        invalidate_obj(instance)
        # TODO: more granular invalidation for referenced models
        invalidate_model(model)


installed = False

def install_cacheops():
    """
    Installs cacheops by numerous monkey patches
    """
    global installed
    if installed:
        return # just return for now, second call is probably done due cycle imports
    installed = True

    monkey_mix(Manager, ManagerMixin)
    monkey_mix(QuerySet, QuerySetMixin)
    monkey_mix(ValuesQuerySet, QuerySetMixin, ['iterator'])
    monkey_mix(ValuesListQuerySet, QuerySetMixin, ['iterator'])
    monkey_mix(DateQuerySet, QuerySetMixin, ['iterator'])

    # Install profile and signal handlers for any earlier created models
    from django.db.models import get_models
    for model in get_models(include_auto_created=True):
        model._default_manager._install_cacheops(model)

    # Turn off caching in admin
    from django.conf import settings
    if 'django.contrib.admin' in settings.INSTALLED_APPS:
        from django.contrib.admin.options import ModelAdmin
        def ModelAdmin_queryset(self, request):
            queryset = o_ModelAdmin_queryset(self, request)
            if queryset._cacheprofile is None:
                return queryset
            else:
                return queryset.nocache()
        o_ModelAdmin_queryset = ModelAdmin.queryset
        ModelAdmin.queryset = ModelAdmin_queryset

    # bind m2m changed handler
    m2m_changed.connect(invalidate_m2m)

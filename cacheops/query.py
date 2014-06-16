# -*- coding: utf-8 -*-
import sys
from functools import wraps
from funcy import cached_property, project
from funcy.py2 import cat, mapcat, map
from .cross import pickle, json, md5

import django
from django.core.exceptions import ImproperlyConfigured
from django.contrib.contenttypes.generic import GenericRel
from django.db.models import Manager, Model
from django.db.models.query import QuerySet, ValuesQuerySet, ValuesListQuerySet, DateQuerySet
from django.db.models.signals import pre_save, post_save, post_delete, m2m_changed
try:
    from django.db.models.query import MAX_GET_RESULTS
except ImportError:
    MAX_GET_RESULTS = None

from .conf import model_profile, redis_client, handle_connection_failure, STRICT_STRINGIFY
from .utils import monkey_mix, dnfs, get_model_name, non_proxy, stamp_fields, load_script, \
                   func_cache_key, cached_view_fab
from .invalidation import invalidate_obj, invalidate_model, invalidate_dict


__all__ = ('cached_as', 'cached_view_as', 'install_cacheops')

_local_get_cache = {}


@handle_connection_failure
def cache_thing(cache_key, data, cond_dnfs, timeout):
    """
    Writes data to cache and creates appropriate invalidators.
    """
    load_script('cache_thing')(
        keys=[cache_key],
        args=[
            pickle.dumps(data, -1),
            json.dumps(cond_dnfs, default=str),
            timeout,
            # model._cacheprofile['timeout'] + 10
        ]
    )


def _cached_as(*samples, **kwargs):
    """
    Caches results of a function and invalidates them same way as given queryset.
    NOTE: Ignores queryset cached ops settings, just caches.
    """
    timeout = kwargs.get('timeout')
    extra = kwargs.get('extra')
    _get_key =  kwargs.get('_get_key')

    # If we unexpectedly get list instead of queryset return identity decorator.
    # Paginator could do this when page.object_list is empty.
    # TODO: think of better way doing this.
    if len(samples) == 1 and isinstance(samples[0], list):
        return lambda func: func

    def _get_queryset(sample):
        if isinstance(sample, Model):
            queryset = sample.__class__.objects.inplace().filter(pk=sample.pk)
        elif isinstance(sample, type) and issubclass(sample, Model):
            queryset = sample.objects.all()
        else:
            queryset = sample

        queryset._require_cacheprofile()

        return queryset

    querysets = map(_get_queryset, samples)
    cond_dnfs = mapcat(dnfs, querysets)
    key_extra = [qs._cache_key() for qs in querysets]
    key_extra.append(extra)
    if not timeout:
        timeout = min(qs._cacheconf['timeout'] for qs in querysets)

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            cache_key = 'as:' + _get_key(func, args, kwargs, key_extra)

            cache_data = redis_client.get(cache_key)
            if cache_data is not None:
                return pickle.loads(cache_data)

            result = func(*args)
            cache_thing(cache_key, result, cond_dnfs, timeout)
            return result

        return wrapper
    return decorator


def cached_as(*samples, **kwargs):
    kwargs["_get_key"] = func_cache_key
    return _cached_as(*samples, **kwargs)


def cached_view_as(*samples, **kwargs):
    return cached_view_fab(_cached_as)(*samples, **kwargs)


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
    from datetime import datetime, date, time, timedelta
    from decimal import Decimal
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

    # Try to not require geo libs
    try:
        from django.contrib.gis.db.models.sql.where import GeoWhereNode
    except ImportError:
        GeoWhereNode = WhereNode

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

    attrs[WhereNode] = attrs[GeoWhereNode] = attrs[ExpressionNode] \
        = ('connector', 'negated', 'children')
    attrs[SQLEvaluator] = ('expression',)
    attrs[ExtraWhere] = ('sqls', 'params')
    attrs[Aggregate] = ('source', 'is_summary', 'col', 'extra')
    attrs[Date] = ('col', 'lookup_type')
    attrs[F] = ('name',)
    attrs[ManyToOneRel] = attrs[OneToOneRel] = attrs[GenericRel] = ('field',)
    attrs[EverythingNode] = attrs[NothingNode] = ()

    q = Query(None)
    q_keys = q.__dict__.keys()
    q_ignored = ['join_map', 'dupe_avoidance', '_extra_select_cache', '_aggregate_select_cache',
                 'used_aliases']
    attrs[Query] = tuple(sorted( set(q_keys) - set(q_ignored) ))

    try:
        for k, v in attrs.items():
            attrs[k] = map(intern, v)
    except NameError:
        # No intern() in Python 3
        pass

    def encode_object(obj):
        if isinstance(obj, set):
            return sorted(obj)
        elif isinstance(obj, type):
            return '%s.%s' % (obj.__module__, obj.__name__)
        elif hasattr(obj, '__uniq_key__'):
            return (obj.__class__, obj.__uniq_key__())
        elif isinstance(obj, (datetime, date, time, timedelta, Decimal)):
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
        # Fall back for unknown objects
        elif not STRICT_STRINGIFY and hasattr(obj, '__dict__'):
            return (obj.__class__, obj.__dict__)
        else:
            raise TypeError("Can't stringify %s" % repr(obj))

    def stringify_query(query):
        # HACK: Catch TypeError and reraise it as ValueError
        #       since django hides it and behave weird when gets a TypeError in Queryset.iterator()
        try:
            return json.dumps(query, default=encode_object, skipkeys=True,
                                     sort_keys=True, separators=(',', ':'))
        except TypeError as e:
            raise ValueError(*e.args)

    return stringify_query
stringify_query = _stringify_query()


class QuerySetMixin(object):
    @cached_property
    def _cacheprofile(self):
        profile = model_profile(self.model)
        if profile:
            self._cacheconf = profile.copy()
            self._cacheconf['write_only'] = False
        return profile

    @cached_property
    def _cloning(self):
        return 1000

    def get_or_create(self, **kwargs):
        """
        Disabling cache for get or create
        TODO: check whether we can use cache (or write_only) here without causing problems
        """
        return self.nocache()._no_monkey.get_or_create(self, **kwargs)

    def _require_cacheprofile(self):
        if self._cacheprofile is None:
            raise ImproperlyConfigured(
                'Cacheops is not enabled for %s.%s model.\n'
                'If you don\'t want to cache anything by default you can "just_enable" it.'
                    % (self.model._meta.app_label, get_model_name(self.model)))

    def _cache_key(self, extra=''):
        """
        Compute a cache key for this queryset
        """
        md = md5()
        md.update('%s.%s' % (self.__class__.__module__, self.__class__.__name__))
        md.update(stamp_fields(self.model)) # Protect from field list changes in model
        md.update(stringify_query(self.query))
        # If query results differ depending on database
        if self._cacheprofile and not self._cacheprofile['db_agnostic']:
            md.update(self.db)
        if extra:
            md.update(str(extra))
        # 'flat' attribute changes results formatting for ValuesQuerySet
        if hasattr(self, 'flat'):
            md.update(str(self.flat))

        return 'q:%s' % md.hexdigest()

    def _cache_results(self, cache_key, results, timeout=None):
        cond_dnfs = dnfs(self)
        cache_thing(cache_key, results, cond_dnfs, timeout or self._cacheconf['timeout'])

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

        if ops is None:
            ops = ['get', 'fetch', 'count']
        if isinstance(ops, str):
            ops = [ops]
        self._cacheconf['ops'] = set(ops)

        if timeout is not None:
            self._cacheconf['timeout'] = timeout
        if write_only is not None:
            self._cacheconf['write_only'] = write_only

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
        if hasattr(self, '_cacheconf'):
            kwargs.setdefault('_cacheconf', self._cacheconf)

        clone = self._no_monkey._clone(self, klass, setup, **kwargs)
        clone._cloning = self._cloning - 1 if self._cloning else 0
        return clone

    def iterator(self):
        # TODO: do not cache empty queries in Django 1.6
        superiter = self._no_monkey.iterator
        cache_this = self._cacheprofile and 'fetch' in self._cacheconf['ops']

        if cache_this:
            cache_key = self._cache_key()
            if not self._cacheconf['write_only']:
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
        if self._cacheprofile and 'count' in self._cacheconf['ops']:
            # Optmization borrowed from overriden method:
            # if queryset cache is already filled just return its len
            # NOTE: there is no self._iter in Django 1.6+, so we use getattr() for compatibility
            if self._result_cache is not None and not getattr(self, '_iter', None):
                return len(self._result_cache)
            return cached_as(self)(lambda: self._no_monkey.count(self))()
        else:
            return self._no_monkey.count(self)

    def get(self, *args, **kwargs):
        # .get() uses the same .iterator() method to fetch data,
        # so here we add 'fetch' to ops
        if self._cacheprofile and 'get' in self._cacheconf['ops']:
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

            if 'fetch' in self._cacheconf['ops']:
                qs = self
            else:
                qs = self._clone().cache()
        else:
            qs = self

        return qs._no_monkey.get(qs, *args, **kwargs)


# This will help with thread-safe cacheprofiles installation and _old_objs access
import threading
_install_lock = threading.Lock()
_installed_classes = set()
_old_objs = {}

def get_thread_id():
    return threading.current_thread().ident


class ManagerMixin(object):
    def _install_cacheops(self, cls):
        with _install_lock:
            if cls not in _installed_classes:
                _installed_classes.add(cls)

                cls._cacheprofile = model_profile(cls)
                if cls._cacheprofile is not None:
                    # Set up signals
                    pre_save.connect(self._pre_save, sender=cls)
                    post_save.connect(self._post_save, sender=cls)
                    post_delete.connect(self._post_delete, sender=cls)

                    # Install auto-created models as their module attributes to make them picklable
                    module = sys.modules[cls.__module__]
                    if not hasattr(module, cls.__name__):
                        setattr(module, cls.__name__, cls)

    def contribute_to_class(self, cls, name):
        self._no_monkey.contribute_to_class(self, cls, name)
        self._install_cacheops(cls)

    def _pre_save(self, sender, instance, **kwargs):
        if instance.pk is not None:
            try:
                _old_objs[get_thread_id(), sender, instance.pk] = sender.objects.get(pk=instance.pk)
            except sender.DoesNotExist:
                pass

    def _post_save(self, sender, instance, **kwargs):
        # Invoke invalidations for both old and new versions of saved object
        old = _old_objs.pop((get_thread_id(), sender, instance.pk), None)
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
            unwanted_attrs = [k for k in instance.__dict__
                                if k.startswith('_') and k.endswith('_cache')]
            unwanted_dict = project(instance.__dict__, unwanted_attrs)
            for k in unwanted_attrs:
                del instance.__dict__[k]

            key = 'pk' if cache_on_save is True else cache_on_save
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
        def get_queryset(self):
            return self.get_query_set()

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
    # Skip this machinery for explicit through tables,
    # since post_save and post_delete events are triggered for them
    if not sender._meta.auto_created:
        return

    # TODO: optimize several invalidate_objs/dicts at once
    if action == 'pre_clear':
        attname = get_model_name(instance)
        objects = sender.objects.filter(**{attname: instance.pk})
        for obj in objects:
            invalidate_obj(obj)
    elif action in ('post_add', 'pre_remove'):
        # NOTE: we don't need to query through objects here,
        #       cause we already know all their meaningful attributes.
        base_att = get_model_name(instance) + '_id'
        item_att = get_model_name(model) + '_id'
        for pk in pk_set:
            invalidate_dict(sender, {base_att: instance.pk, item_att: pk})


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
    QuerySet._cacheprofile = QuerySetMixin._cacheprofile
    QuerySet._cloning = QuerySetMixin._cloning
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
            return o_ModelAdmin_queryset(self, request).nocache()
        o_ModelAdmin_queryset = ModelAdmin.queryset
        ModelAdmin.queryset = ModelAdmin_queryset

    # bind m2m changed handler
    m2m_changed.connect(invalidate_m2m)

# -*- coding: utf-8 -*-
import sys
from functools import wraps
import json
import six
from funcy import cached_property, project, once, once_per, monkey
from funcy.py2 import mapcat, map
from .cross import pickle, md5

import django
from django.utils.encoding import smart_str
from django.core.exceptions import ImproperlyConfigured
from django.db.models import Manager, Model
from django.db.models.query import QuerySet
from django.db.models.sql.datastructures import EmptyResultSet
from django.db.models.signals import pre_save, post_save, post_delete, m2m_changed
try:
    from django.db.models.query import MAX_GET_RESULTS
except ImportError:
    MAX_GET_RESULTS = None

from .conf import model_profile, redis_client, handle_connection_failure, LRU, ALL_OPS
from .utils import monkey_mix, get_model_name, stamp_fields, load_script, \
                   func_cache_key, cached_view_fab, get_thread_id
from .tree import dnfs
from .invalidation import invalidate_obj, invalidate_dict


__all__ = ('cached_as', 'cached_view_as', 'install_cacheops')

_local_get_cache = {}


@handle_connection_failure
def cache_thing(cache_key, data, cond_dnfs, timeout):
    """
    Writes data to cache and creates appropriate invalidators.
    """
    load_script('cache_thing', LRU)(
        keys=[cache_key],
        args=[
            pickle.dumps(data, -1),
            json.dumps(cond_dnfs, default=str),
            timeout
        ]
    )


def _cached_as(*samples, **kwargs):
    """
    Caches results of a function and invalidates them same way as given queryset.
    NOTE: Ignores queryset cached ops settings, just caches.
    """
    timeout = kwargs.get('timeout')
    extra = kwargs.get('extra')
    _get_key = kwargs.get('_get_key')

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

    def _require_cacheprofile(self):
        if self._cacheprofile is None:
            raise ImproperlyConfigured(
                'Cacheops is not enabled for %s.%s model.\n'
                'If you don\'t want to cache anything by default '
                'you can configure it with empty ops.'
                    % (self.model._meta.app_label, get_model_name(self.model)))

    def _cache_key(self, extra=''):
        """
        Compute a cache key for this queryset
        """
        md = md5()
        md.update('%s.%s' % (self.__class__.__module__, self.__class__.__name__))
        md.update(stamp_fields(self.model)) # Protect from field list changes in model
        # Use query SQL as part of a key
        try:
            md.update(smart_str(self.query))
        except EmptyResultSet:
            pass
        # If query results differ depending on database
        if self._cacheprofile and not self._cacheprofile['db_agnostic']:
            md.update(self.db)
        if extra:
            md.update(str(extra))
        # Need to test for attribute existence cause Django 1.8 and earlier
        if hasattr(self, '_iterator_class'):
            it_class = self._iterator_class
            md.update('%s.%s' % (it_class.__module__, it_class.__name__))
        # 'flat' attribute changes results formatting for values_list() in Django 1.8 and earlier
        if hasattr(self, 'flat'):
            md.update(str(self.flat))

        return 'q:%s' % md.hexdigest()

    def _cache_results(self, cache_key, results):
        cond_dnfs = dnfs(self)
        cache_thing(cache_key, results, cond_dnfs, self._cacheconf['timeout'])

    def cache(self, ops=None, timeout=None, write_only=None):
        """
        Enables caching for given ops
            ops        - a subset of {'get', 'fetch', 'count', 'exists'},
                         ops caching to be turned on, all enabled by default
            timeout    - override default cache timeout
            write_only - don't try fetching from cache, still write result there

        NOTE: you actually can disable caching by omiting corresponding ops,
              .cache(ops=[]) disables caching for this queryset.
        """
        self._require_cacheprofile()

        if ops is None or ops == 'all':
            ops = ALL_OPS
        if isinstance(ops, str):
            ops = [ops]
        self._cacheconf['ops'] = set(ops)

        if timeout is not None:
            self._cacheconf['timeout'] = timeout
        if write_only is not None:
            self._cacheconf['write_only'] = write_only

        return self

    def nocache(self):
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

    if django.VERSION >= (1, 9):
        def _clone(self, **kwargs):
            if self._cloning:
                return self.clone(**kwargs)
            else:
                self.__dict__.update(kwargs)
                return self

        def clone(self, **kwargs):
            kwargs.setdefault('_cacheprofile', self._cacheprofile)
            if hasattr(self, '_cacheconf'):
                kwargs.setdefault('_cacheconf', self._cacheconf)

            clone = self._no_monkey._clone(self, **kwargs)
            clone._cloning = self._cloning - 1 if self._cloning else 0
            return clone
    else:
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
            if not self._cacheconf['write_only'] and not self._for_write:
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
            if self._cacheprofile['local_get']        \
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
                    # If some arg is unhashable we can't save it to dict key,
                    # we just skip local cache in that case
                    pass

            if 'fetch' in self._cacheconf['ops']:
                qs = self
            else:
                qs = self._clone().cache()
        else:
            qs = self

        return qs._no_monkey.get(qs, *args, **kwargs)

    if django.VERSION >= (1, 6):
        def exists(self):
            if self._cacheprofile and 'exists' in self._cacheconf['ops']:
                if self._result_cache is not None:
                    return bool(self._result_cache)
                return cached_as(self)(lambda: self._no_monkey.exists(self))()
            else:
                return self._no_monkey.exists(self)

    def bulk_create(self, objs, batch_size=None):
        self._no_monkey.bulk_create(self, objs, batch_size=batch_size)
        for obj in objs:
            invalidate_obj(obj)


# We need to stash old object before Model.save() to invalidate on its properties
_old_objs = {}

class ManagerMixin(object):
    @once_per('cls')
    def _install_cacheops(self, cls):
        # Django 1.7 migrations create lots of fake models, just skip them
        if cls.__module__ == '__fake__':
            return

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
    if not hasattr(Manager, 'get_queryset'):
        def get_queryset(self):
            return self.get_query_set()

    def inplace(self):
        return self.get_queryset().inplace()

    def get(self, *args, **kwargs):
        return self.get_queryset().inplace().get(*args, **kwargs)

    def cache(self, *args, **kwargs):
        return self.get_queryset().cache(*args, **kwargs)

    def nocache(self):
        return self.get_queryset().nocache()


def invalidate_m2m(sender=None, instance=None, model=None, action=None, pk_set=None, **kwargs):
    """
    Invoke invalidation on m2m changes.
    """
    # Skip this machinery for explicit through tables,
    # since post_save and post_delete events are triggered for them
    if not sender._meta.auto_created:
        return

    m2m = next(m2m for m2m in instance._meta.many_to_many + model._meta.many_to_many
                   if m2m.rel.through == sender)

    # TODO: optimize several invalidate_objs/dicts at once
    if action == 'pre_clear':
        objects = sender.objects.filter(**{m2m.m2m_field_name(): instance.pk})
        for obj in objects:
            invalidate_obj(obj)
    elif action in ('post_add', 'pre_remove'):
        # NOTE: we don't need to query through objects here,
        #       cause we already know all their meaningfull attributes.
        for pk in pk_set:
            invalidate_dict(sender, {
                m2m.m2m_column_name(): instance.pk,
                m2m.m2m_reverse_name(): pk
            })


@once
def install_cacheops():
    """
    Installs cacheops by numerous monkey patches
    """
    monkey_mix(Manager, ManagerMixin)
    monkey_mix(QuerySet, QuerySetMixin)
    QuerySet._cacheprofile = QuerySetMixin._cacheprofile
    QuerySet._cloning = QuerySetMixin._cloning

    # DateQuerySet existed in Django 1.7 and earlier
    # Values*QuerySet existed in Django 1.8 and earlier
    from django.db.models import query
    for cls_name in ('ValuesQuerySet', 'ValuesListQuerySet', 'DateQuerySet'):
        if hasattr(query, cls_name):
            cls = getattr(query, cls_name)
            monkey_mix(cls, QuerySetMixin, ['iterator'])

    try:
        # Use app registry in Django 1.7
        from django.apps import apps
        admin_used = apps.is_installed('django.contrib.admin')
        get_models = apps.get_models
    except ImportError:
        # Introspect INSTALLED_APPS in older djangos
        from django.conf import settings
        admin_used = 'django.contrib.admin' in settings.INSTALLED_APPS
        from django.db.models import get_models

    # Install profile and signal handlers for any earlier created models
    for model in get_models(include_auto_created=True):
        model._default_manager._install_cacheops(model)

    # Turn off caching in admin
    if admin_used:
        from django.contrib.admin.options import ModelAdmin

        # Renamed queryset to get_queryset in Django 1.6
        method_name = 'get_queryset' if hasattr(ModelAdmin, 'get_queryset') else 'queryset'

        @monkey(ModelAdmin, name=method_name)
        def get_queryset(self, request):
            return get_queryset.original(self, request).nocache()

    # Bind m2m changed handler
    m2m_changed.connect(invalidate_m2m)

    # Make buffers/memoryviews pickleable to serialize binary field data
    if six.PY2:
        import copy_reg
        copy_reg.pickle(buffer, lambda b: (buffer, (bytes(b),)))
    if six.PY3:
        import copyreg
        copyreg.pickle(memoryview, lambda b: (memoryview, (bytes(b),)))

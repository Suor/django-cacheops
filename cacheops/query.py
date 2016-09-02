# -*- coding: utf-8 -*-
import sys
import json
import threading
import six
from funcy import select_keys, cached_property, once, once_per, monkey, wraps, walk
from funcy.py2 import mapcat, map
from .cross import pickle, md5

import django
from django.utils.encoding import smart_str, force_text
from django.core.exceptions import ImproperlyConfigured
from django.db.models import Manager, Model
from django.db.models.query import QuerySet
from django.db.models.sql.datastructures import EmptyResultSet
from django.db.models.signals import pre_save, post_save, post_delete, m2m_changed
# This thing was removed in Django 1.8
try:
    from django.db.models.query import MAX_GET_RESULTS
except ImportError:
    MAX_GET_RESULTS = None

from .conf import model_profile, model_is_fake, settings, ALL_OPS
from .utils import monkey_mix, stamp_fields, func_cache_key, cached_view_fab, family_has_profile
from .redis import redis_client, handle_connection_failure, load_script
from .tree import dnfs
from .invalidation import invalidate_obj, invalidate_dict, no_invalidation
from .transaction import in_transaction
from .signals import cache_read


__all__ = ('cached_as', 'cached_view_as', 'install_cacheops')

_local_get_cache = {}


@handle_connection_failure
def cache_thing(cache_key, data, cond_dnfs, timeout):
    """
    Writes data to cache and creates appropriate invalidators.
    """
    assert not in_transaction()
    load_script('cache_thing', settings.CACHEOPS_LRU)(
        keys=[cache_key],
        args=[
            pickle.dumps(data, -1),
            json.dumps(cond_dnfs, default=str),
            timeout
        ]
    )


def cached_as(*samples, **kwargs):
    """
    Caches results of a function and invalidates them same way as given queryset.
    NOTE: Ignores queryset cached ops settings, just caches.
    """
    timeout = kwargs.get('timeout')
    extra = kwargs.get('extra')
    key_func = kwargs.get('key_func', func_cache_key)

    # If we unexpectedly get list instead of queryset return identity decorator.
    # Paginator could do this when page.object_list is empty.
    if len(samples) == 1 and isinstance(samples[0], list):
        return lambda func: func

    def _get_queryset(sample):
        if isinstance(sample, Model):
            queryset = sample.__class__.objects.filter(pk=sample.pk)
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
        timeout = min(qs._cacheprofile['timeout'] for qs in querysets)

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if in_transaction() or not settings.CACHEOPS_ENABLED:
                return func(*args, **kwargs)

            cache_key = 'as:' + key_func(func, args, kwargs, key_extra)

            cache_data = redis_client.get(cache_key)
            cache_read.send(sender=None, func=func, hit=cache_data is not None)
            if cache_data is not None:
                return pickle.loads(cache_data)

            result = func(*args, **kwargs)
            cache_thing(cache_key, result, cond_dnfs, timeout)
            return result

        return wrapper
    return decorator


def cached_view_as(*samples, **kwargs):
    return cached_view_fab(cached_as)(*samples, **kwargs)


class QuerySetMixin(object):
    @cached_property
    def _cacheprofile(self):
        profile = model_profile(self.model)
        return profile.copy() if profile else None

    @cached_property
    def _cloning(self):
        return 1000

    def _require_cacheprofile(self):
        if self._cacheprofile is None:
            raise ImproperlyConfigured(
                'Cacheops is not enabled for %s.%s model.\n'
                'If you don\'t want to cache anything by default '
                'you can configure it with empty ops.'
                    % (self.model._meta.app_label, self.model._meta.model_name))

    def _cache_key(self):
        """
        Compute a cache key for this queryset
        """
        md = md5()
        md.update('%s.%s' % (self.__class__.__module__, self.__class__.__name__))
        # Vary cache key for proxy models
        md.update('%s.%s' % (self.model.__module__, self.model.__name__))
        # Protect from field list changes in model
        md.update(stamp_fields(self.model))
        # Use query SQL as part of a key
        try:
            sql, params = self.query.get_compiler(self.db).as_sql()
            try:
                sql_str = sql % params
            except UnicodeDecodeError:
                sql_str = sql % walk(force_text, params)
            md.update(smart_str(sql_str))
        except EmptyResultSet:
            pass
        # If query results differ depending on database
        if self._cacheprofile and not self._cacheprofile['db_agnostic']:
            md.update(self.db)
        # Thing only appeared in Django 1.9
        it_class = getattr(self, '_iterable_class', None)
        if it_class:
            md.update('%s.%s' % (it_class.__module__, it_class.__name__))
        # 'flat' attribute changes results formatting for values_list() in Django 1.8 and earlier
        if hasattr(self, 'flat'):
            md.update(str(self.flat))

        return 'q:%s' % md.hexdigest()

    def _cache_results(self, cache_key, results):
        cond_dnfs = dnfs(self)
        cache_thing(cache_key, results, cond_dnfs, self._cacheprofile['timeout'])

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
            ops = {ops}
        self._cacheprofile['ops'] = set(ops)

        if timeout is not None:
            self._cacheprofile['timeout'] = timeout
        if write_only is not None:
            self._cacheprofile['write_only'] = write_only

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
            # NOTE: need to copy profile so that clone changes won't affect this queryset
            if '_cacheprofile' in self.__dict__ and self._cacheprofile:
                kwargs.setdefault('_cacheprofile', self._cacheprofile.copy())

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
            if '_cacheprofile' in self.__dict__ and self._cacheprofile:
                kwargs.setdefault('_cacheprofile', self._cacheprofile.copy())

            clone = self._no_monkey._clone(self, klass, setup, **kwargs)
            clone._cloning = self._cloning - 1 if self._cloning else 0
            return clone

    def iterator(self):
        # If cache is not enabled or in transaction just fall back
        if not self._cacheprofile or 'fetch' not in self._cacheprofile['ops'] \
                or in_transaction() or not settings.CACHEOPS_ENABLED:
            return self._no_monkey.iterator(self)

        cache_key = self._cache_key()
        if not self._cacheprofile['write_only'] and not self._for_write:
            # Trying get data from cache
            cache_data = redis_client.get(cache_key)
            cache_read.send(sender=self.model, func=None, hit=cache_data is not None)
            if cache_data is not None:
                return iter(pickle.loads(cache_data))

        # Cache miss - fetch data from overriden implementation
        def iterate():
            # NOTE: we are using self._result_cache to avoid fetching-while-fetching bug #177
            self._result_cache = []
            for obj in self._no_monkey.iterator(self):
                self._result_cache.append(obj)
                yield obj
            self._cache_results(cache_key, self._result_cache)

        return iterate()

    def count(self):
        if self._cacheprofile and 'count' in self._cacheprofile['ops']:
            # Optmization borrowed from overriden method:
            # if queryset cache is already filled just return its len
            if self._result_cache is not None:
                return len(self._result_cache)
            return cached_as(self)(lambda: self._no_monkey.count(self))()
        else:
            return self._no_monkey.count(self)

    def get(self, *args, **kwargs):
        # .get() uses the same .iterator() method to fetch data,
        # so here we add 'fetch' to ops
        if self._cacheprofile and 'get' in self._cacheprofile['ops']:
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

            if 'fetch' in self._cacheprofile['ops']:
                qs = self
            else:
                qs = self._clone().cache()
        else:
            qs = self

        return qs._no_monkey.get(qs, *args, **kwargs)

    def exists(self):
        if self._cacheprofile and 'exists' in self._cacheprofile['ops']:
            if self._result_cache is not None:
                return bool(self._result_cache)
            return cached_as(self)(lambda: self._no_monkey.exists(self))()
        else:
            return self._no_monkey.exists(self)

    def bulk_create(self, objs, batch_size=None):
        objs = self._no_monkey.bulk_create(self, objs, batch_size=batch_size)
        if family_has_profile(self.model):
            for obj in objs:
                invalidate_obj(obj)
        return objs

    def invalidated_update(self, **kwargs):
        clone = self._clone().nocache()
        clone._for_write = True  # affects routing

        objects = list(clone.iterator())  # bypass queryset cache
        rows = clone.update(**kwargs)
        objects.extend(clone.iterator())
        for obj in objects:
            invalidate_obj(obj)
        return rows


def connect_first(signal, receiver, sender):
    old_receivers = signal.receivers
    signal.receivers = []
    signal.connect(receiver, sender=sender)
    signal.receivers += old_receivers

# We need to stash old object before Model.save() to invalidate on its properties
_old_objs = threading.local()

class ManagerMixin(object):
    @once_per('cls')
    def _install_cacheops(self, cls):
        if family_has_profile(cls):
            # Set up signals
            connect_first(pre_save, self._pre_save, sender=cls)
            connect_first(post_save, self._post_save, sender=cls)
            connect_first(post_delete, self._post_delete, sender=cls)

            # Install auto-created models as their module attributes to make them picklable
            module = sys.modules[cls.__module__]
            if not hasattr(module, cls.__name__):
                setattr(module, cls.__name__, cls)

    def contribute_to_class(self, cls, name):
        self._no_monkey.contribute_to_class(self, cls, name)
        # Django 1.7+ migrations create lots of fake models, just skip them
        # NOTE: we make it here rather then inside _install_cacheops()
        #       because we don't want @once_per() to hold refs to all of them.
        if not model_is_fake(cls):
            self._install_cacheops(cls)

    def _pre_save(self, sender, instance, **kwargs):
        if instance.pk is not None and not no_invalidation.active:
            try:
                _old_objs.__dict__[sender, instance.pk] = sender.objects.get(pk=instance.pk)
            except sender.DoesNotExist:
                pass

    def _post_save(self, sender, instance, **kwargs):
        # Invoke invalidations for both old and new versions of saved object
        old = _old_objs.__dict__.pop((sender, instance.pk), None)
        if old:
            invalidate_obj(old)
        invalidate_obj(instance)

        if in_transaction() or not settings.CACHEOPS_ENABLED:
            return

        # NOTE: it's possible for this to be a subclass, e.g. proxy, without cacheprofile,
        #       but its base having one. Or vice versa.
        #       We still need to invalidate in this case, but cache on save better be skipped.
        cacheprofile = model_profile(instance.__class__)
        if not cacheprofile:
            return

        # Enabled cache_on_save makes us write saved object to cache.
        # Later it can be retrieved with .get(<cache_on_save_field>=<value>)
        # <cache_on_save_field> is pk unless specified.
        # This sweet trick saves a db request and helps with slave lag.
        cache_on_save = cacheprofile.get('cache_on_save')
        if cache_on_save:
            # HACK: We get this object "from field" so it can contain
            #       some undesirable attributes or other objects attached.
            #       RelatedField accessors do that, for example.
            #
            #       So we strip down any _*_cache attrs before saving
            #       and later reassign them
            unwanted_dict = select_keys(r'^_.*_cache$', instance.__dict__)
            for k in unwanted_dict:
                del instance.__dict__[k]

            key = 'pk' if cache_on_save is True else cache_on_save
            cond = {key: getattr(instance, key)}
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

    def inplace(self):
        return self.get_queryset().inplace()

    def cache(self, *args, **kwargs):
        return self.get_queryset().cache(*args, **kwargs)

    def nocache(self):
        return self.get_queryset().nocache()

    def invalidated_update(self, **kwargs):
        return self.get_queryset().inplace().invalidated_update(**kwargs)


def invalidate_m2m(sender=None, instance=None, model=None, action=None, pk_set=None, reverse=None,
                   **kwargs):
    """
    Invoke invalidation on m2m changes.
    """
    # Skip this machinery for explicit through tables,
    # since post_save and post_delete events are triggered for them
    if not sender._meta.auto_created:
        return
    if action not in ('pre_clear', 'post_add', 'pre_remove'):
        return

    m2m = next(m2m for m2m in instance._meta.many_to_many + model._meta.many_to_many
                   if m2m.rel.through == sender)

    # TODO: optimize several invalidate_objs/dicts at once
    if action == 'pre_clear':
        # TODO: always use column names here once Django 1.3 is dropped
        instance_field = m2m.m2m_reverse_field_name() if reverse else m2m.m2m_field_name()
        objects = sender.objects.filter(**{instance_field: instance.pk})
        for obj in objects:
            invalidate_obj(obj)
    elif action in ('post_add', 'pre_remove'):
        instance_column, model_column = m2m.m2m_column_name(), m2m.m2m_reverse_name()
        if reverse:
            instance_column, model_column = model_column, instance_column
        # NOTE: we don't need to query through objects here,
        #       cause we already know all their meaningfull attributes.
        for pk in pk_set:
            invalidate_dict(sender, {
                instance_column: instance.pk,
                model_column: pk
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

    # Use app registry to introspect used apps
    from django.apps import apps

    # Install profile and signal handlers for any earlier created models
    for model in apps.get_models(include_auto_created=True):
        model._default_manager._install_cacheops(model)

    # Turn off caching in admin
    if apps.is_installed('django.contrib.admin'):
        from django.contrib.admin.options import ModelAdmin

        @monkey(ModelAdmin)
        def get_queryset(self, request):
            return get_queryset.original(self, request).nocache()

    # Bind m2m changed handler
    m2m_changed.connect(invalidate_m2m)

    # Make buffers/memoryviews pickleable to serialize binary field data
    if six.PY2:
        import copy_reg
        copy_reg.pickle(buffer, lambda b: (buffer, (bytes(b),)))  # noqa
    if six.PY3:
        import copyreg
        copyreg.pickle(memoryview, lambda b: (memoryview, (bytes(b),)))

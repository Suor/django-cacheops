import sys
import threading
from random import random

from funcy import select_keys, cached_property, once, once_per, monkey, wraps, walk, chain
from funcy import lmap, lcat, join_with

from django.utils.encoding import force_str
from django.core.exceptions import ImproperlyConfigured, EmptyResultSet
from django.db import DEFAULT_DB_ALIAS, connections, models
from django.db.models.manager import BaseManager
from django.db.models.query import MAX_GET_RESULTS
from django.db.models.signals import pre_save, post_save, post_delete, m2m_changed
from django.db.transaction import atomic

from .conf import model_profile, settings, ALL_OPS
from .utils import monkey_mix, stamp_fields, get_cache_key, cached_view_fab, family_has_profile
from .utils import md5
from .getset import cache_thing, getting
from .sharding import get_prefix
from .tree import dnfs
from .invalidation import invalidate_obj, invalidate_dict, skip_on_no_invalidation
from .transaction import transaction_states
from .signals import cache_read


__all__ = ('cached_as', 'cached_view_as', 'install_cacheops')

_local_get_cache = {}


def cached_as(*samples, timeout=None, extra=None, lock=None, keep_fresh=False):
    """
    Caches results of a function and invalidates them same way as given queryset(s).
    NOTE: Ignores queryset cached ops settings, always caches.

    If keep_fresh is True, this will prevent caching if the given querysets are
    invalidated during the function call. This prevents prolonged caching of
    stale data.
    """
    if not samples:
        raise TypeError('Pass a queryset, a model or an object to cache like')

    # If we unexpectedly get list instead of queryset return identity decorator.
    # Paginator could do this when page.object_list is empty.
    if len(samples) == 1 and isinstance(samples[0], list):
        return lambda func: func

    def _get_queryset(sample):
        if isinstance(sample, models.Model):
            queryset = sample.__class__.objects.filter(pk=sample.pk)
        elif isinstance(sample, type) and issubclass(sample, models.Model):
            queryset = sample.objects.all()
        else:
            queryset = sample

        queryset._require_cacheprofile()

        return queryset

    querysets = lmap(_get_queryset, samples)
    dbs = list({qs.db for qs in querysets})
    cond_dnfs = join_with(lcat, map(dnfs, querysets))  # TODO: use cached version?
    qs_keys = [qs._cache_key(prefix=False) for qs in querysets]
    if timeout is None:
        timeout = min(qs._cacheprofile['timeout'] for qs in querysets)
    if lock is None:
        lock = any(qs._cacheprofile['lock'] for qs in querysets)

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not settings.CACHEOPS_ENABLED or transaction_states.is_dirty(dbs):
                return func(*args, **kwargs)

            prefix = get_prefix(func=func, _cond_dnfs=cond_dnfs, dbs=dbs)
            extra_val = extra(*args, **kwargs) if callable(extra) else extra
            cache_key = prefix + 'as:' + get_cache_key(func, args, kwargs, qs_keys, extra_val)

            with getting(cache_key, cond_dnfs, prefix, lock=lock) as cache_data:
                cache_read.send(sender=None, func=func, hit=cache_data is not None)
                if cache_data is not None:
                    return settings.CACHEOPS_SERIALIZER.loads(cache_data)
                else:
                    precall_key = ''
                    expected_checksum = ''
                    if keep_fresh and settings.CACHEOPS_INSIDEOUT:
                        # The conj stamps should not be dropped while we calculate the function.
                        # But being filled in concurrently is a normal concurrent cache write.
                        # However, if they are filled in and then dropped, we cannot detect that.
                        # Unless we fill them ourselves and get expected checksum now. We also need
                        # to fill in schemes, so we just reuse the cache_thing().
                        expected_checksum = cache_thing(prefix, cache_key, '', cond_dnfs, timeout,
                                                        dbs=dbs, expected_checksum='never match')
                    elif keep_fresh:
                        # We call this "asp" for "as precall" because this key is
                        # cached before the actual function is called. We randomize
                        # the key to prevent falsely thinking the key was not
                        # invalidated when in fact it was invalidated and the
                        # function was called again in another process.
                        suffix = get_cache_key(func, args, kwargs, qs_keys, extra_val, random())
                        precall_key = prefix + 'asp:' + suffix
                        # Cache a precall_key to watch for invalidation during
                        # the function call. Its value does not matter. If and
                        # only if it remains valid before, during, and after the
                        # call, the result can be cached and returned.
                        cache_thing(prefix, precall_key, 'PRECALL', cond_dnfs, timeout, dbs=dbs)

                    result = func(*args, **kwargs)
                    cache_thing(prefix, cache_key, result, cond_dnfs, timeout, dbs=dbs,
                                precall_key=precall_key, expected_checksum=expected_checksum)
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

    def _cache_key(self, prefix=True):
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
                sql_str = sql % walk(force_str, params)
            md.update(force_str(sql_str))
        except EmptyResultSet:
            pass
        # If query results differ depending on database
        if self._cacheprofile and not self._cacheprofile['db_agnostic']:
            md.update(self.db)
        # Iterable class pack results differently
        it_class = self._iterable_class
        md.update('%s.%s' % (it_class.__module__, it_class.__name__))

        cache_key = 'q:%s' % md.hexdigest()
        return self._prefix + cache_key if prefix else cache_key

    @cached_property
    def _prefix(self):
        return get_prefix(_queryset=self)

    @cached_property
    def _cond_dnfs(self):
        return dnfs(self)

    def _cache_results(self, cache_key, results):
        cache_thing(self._prefix, cache_key, results,
                    self._cond_dnfs, self._cacheprofile['timeout'], dbs=[self.db])

    def _should_cache(self, op):
        # If cache and op are enabled and not within write or dirty transaction
        return settings.CACHEOPS_ENABLED \
            and self._cacheprofile and op in self._cacheprofile['ops'] \
            and not self._for_write \
            and not transaction_states[self.db].is_dirty()

    def cache(self, ops=None, timeout=None, lock=None):
        """
        Enables caching for given ops
            ops        - a subset of {'get', 'fetch', 'count', 'exists', 'aggregate'},
                         ops caching to be turned on, all enabled by default
            timeout    - override default cache timeout
            lock       - use lock to prevent dog-pile effect

        NOTE: you actually can disable caching by omitting corresponding ops,
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
        if lock is not None:
            self._cacheprofile['lock'] = lock

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

    def _clone(self, **kwargs):
        if self._cloning:
            return self.clone(**kwargs)
        else:
            self.__dict__.update(kwargs)
            return self

    def clone(self, **kwargs):
        clone = self._no_monkey._clone(self, **kwargs)
        clone._cloning = self._cloning - 1 if self._cloning else 0
        # NOTE: need to copy profile so that clone changes won't affect this queryset
        if self.__dict__.get('_cacheprofile'):
            clone._cacheprofile = self._cacheprofile.copy()
        return clone

    def _fetch_all(self):
        # If already fetched or should pass by then fall back
        if self._result_cache is not None or not self._should_cache('fetch'):
            return self._no_monkey._fetch_all(self)

        cache_key = self._cache_key()
        lock = self._cacheprofile['lock']

        with getting(cache_key, self._cond_dnfs, self._prefix, lock=lock) as cache_data:
            cache_read.send(sender=self.model, func=None, hit=cache_data is not None)
            if cache_data is not None:
                self._result_cache = settings.CACHEOPS_SERIALIZER.loads(cache_data)
            else:
                self._result_cache = list(self._iterable_class(self))
                self._cache_results(cache_key, self._result_cache)

        return self._no_monkey._fetch_all(self)

    def count(self):
        if self._should_cache('count'):
            # Optmization borrowed from overridden method:
            # if queryset cache is already filled just return its len
            if self._result_cache is not None:
                return len(self._result_cache)
            return cached_as(self)(lambda: self._no_monkey.count(self))()
        else:
            return self._no_monkey.count(self)

    def aggregate(self, *args, **kwargs):
        if self._should_cache('aggregate'):
            # We resolve aggregates to add joins, which will affect query DNF
            qs = self._clone()
            for aggregate_expr in chain(args, kwargs.values()):
                aggregate_expr.resolve_expression(
                    qs.query, allow_joins=True, reuse=None, summarize=True)

            # Use resulting qs as a ref
            return cached_as(qs)(lambda: self._no_monkey.aggregate(self, *args, **kwargs))()
        else:
            return self._no_monkey.aggregate(self, *args, **kwargs)

    def get(self, *args, **kwargs):
        # .get() uses the same ._fetch_all() method to fetch data,
        # so here we add 'fetch' to ops
        if self._should_cache('get'):
            # NOTE: local_get=True enables caching of simple gets in local memory,
            #       which is very fast, but not invalidated.
            # Don't bother with Q-objects, select_related and previous filters,
            # simple gets - thats what we are really up to here.
            #
            # TODO: this checks are far from adequate, at least these are missed:
            #       - self._fields (values, values_list)
            #       - annotations
            #       - ...
            # TODO: don't distinguish between pk, pk__exaxt, id, id__exact
            # TOOD: work with .filter(**kwargs).get() ?
            if self._cacheprofile['local_get']        \
                    and not args                      \
                    and not self.query.select_related \
                    and not self.query.where.children:
                # NOTE: We use simpler way to generate a cache key to cut costs.
                #       Some day it could produce same key for different requests.
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

    def first(self):
        if self._should_cache('get'):
            return self._no_monkey.first(self._clone().cache())
        return self._no_monkey.first(self)

    def last(self):
        if self._should_cache('get'):
            return self._no_monkey.last(self._clone().cache())
        return self._no_monkey.last(self)

    def exists(self):
        if self._should_cache('exists'):
            if self._result_cache is not None:
                return bool(self._result_cache)
            return cached_as(self)(lambda: self._no_monkey.exists(self))()
        else:
            return self._no_monkey.exists(self)

    def bulk_create(self, objs, *args, **kwargs):
        objs = self._no_monkey.bulk_create(self, objs, *args, **kwargs)
        if family_has_profile(self.model):
            for obj in objs:
                invalidate_obj(obj, using=self.db)
        return objs

    def invalidated_update(self, **kwargs):
        clone = self._clone().nocache().select_related(None)
        clone._for_write = True  # affects routing

        with atomic(using=clone.db):
            objects = list(clone.select_for_update())
            rows = clone.update(**kwargs)

            # TODO: do not refetch objects but update with kwargs in simple cases?
            # We use clone database to fetch new states, as this is the db they were written to.
            # Using router with new_objects may fail, using self may return slave during lag.
            pks = {obj.pk for obj in objects}
            new_objects = self.model.objects.filter(pk__in=pks).using(clone.db)

        for obj in chain(objects, new_objects):
            invalidate_obj(obj, using=clone.db)

        return rows


def connect_first(signal, receiver, sender):
    old_receivers = signal.receivers
    signal.receivers = []
    signal.connect(receiver, sender=sender, weak=False)
    signal.receivers += old_receivers

# We need to stash old object before Model.save() to invalidate on its properties
_old_objs = threading.local()

class ManagerMixin(object):
    @once_per('cls')
    def _install_cacheops(self, cls):
        # Set up signals
        connect_first(pre_save, self._pre_save, sender=cls)
        connect_first(post_save, self._post_save, sender=cls)
        connect_first(post_delete, self._post_delete, sender=cls)

        # Install auto-created models as their module attributes to make them picklable
        module = sys.modules[cls.__module__]
        if not hasattr(module, cls.__name__):
            setattr(module, cls.__name__, cls)

    # This is probably still needed if models are created dynamically
    def contribute_to_class(self, cls, name):
        self._no_monkey.contribute_to_class(self, cls, name)
        # Django migrations create lots of fake models, just skip them
        # NOTE: we make it here rather then inside _install_cacheops()
        #       because we don't want @once_per() to hold refs to all of them.
        if cls.__module__ != '__fake__' and family_has_profile(cls):
            self._install_cacheops(cls)

    @skip_on_no_invalidation
    def _pre_save(self, sender, instance, using, **kwargs):
        if instance.pk is not None and not instance._state.adding:
            try:
                # TODO: do not fetch non-serializable fields
                _old_objs.__dict__[sender, instance.pk] \
                    = sender.objects.using(using).get(pk=instance.pk)
            except sender.DoesNotExist:
                pass

    @skip_on_no_invalidation
    def _post_save(self, sender, instance, using, **kwargs):
        # Invoke invalidations for both old and new versions of saved object
        old = _old_objs.__dict__.pop((sender, instance.pk), None)
        if old:
            invalidate_obj(old, using=using)
        invalidate_obj(instance, using=using)

        invalidate_o2o(sender, old, instance, using=using)

        # We run invalidations but skip caching if we are dirty
        if transaction_states[using].is_dirty():
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
            qs = sender.objects.inplace().using(using).filter(**cond).order_by()
            # Mimic Django .get() logic
            if MAX_GET_RESULTS and (
                    not qs.query.select_for_update
                    or connections[qs.db].features.supports_select_for_update_with_limit):
                qs.query.set_limits(high=MAX_GET_RESULTS)
            qs._cache_results(qs._cache_key(), [instance])

            # Reverting stripped attributes
            instance.__dict__.update(unwanted_dict)

    def _post_delete(self, sender, instance, using, **kwargs):
        """
        Invalidation upon object deletion.
        """
        # NOTE: this will behave wrong if someone changed object fields
        #       before deletion (why anyone will do that?)
        invalidate_obj(instance, using=using)
        # NOTE: this is needed because m2m_changed is not sent on such deletion:
        #       https://code.djangoproject.com/ticket/17688
        invalidate_m2o(sender, instance, using)

    def inplace(self):
        return self.get_queryset().inplace()

    def cache(self, *args, **kwargs):
        return self.get_queryset().cache(*args, **kwargs)

    def nocache(self):
        return self.get_queryset().nocache()

    def invalidated_update(self, **kwargs):
        return self.get_queryset().inplace().invalidated_update(**kwargs)


def invalidate_o2o(sender, old, instance, using=DEFAULT_DB_ALIAS):
    """Invoke invalidation for o2o reverse queries"""
    o2o_fields = [f for f in sender._meta.fields if isinstance(f, models.OneToOneField)]
    for f in o2o_fields:
        old_value = getattr(old, f.attname, None)
        value = getattr(instance, f.attname)
        if old_value != value:
            rmodel, rfield = f.related_model, f.remote_field.field_name
            if old:
                invalidate_dict(rmodel, {rfield: old_value}, using=using)
            invalidate_dict(rmodel, {rfield: value}, using=using)


def invalidate_m2o(sender, instance, using=DEFAULT_DB_ALIAS):
    """Invoke invalidation for m2o and m2m queries to a deleted instance"""
    all_fields = sender._meta.get_fields(include_hidden=True, include_parents=True)
    m2o_fields = [f for f in all_fields if isinstance(f, models.ManyToOneRel)]
    fk_fields_names_map = {
        f.name: f.attname
        for f in all_fields if isinstance(f, models.ForeignKey)
    }
    for f in m2o_fields:
        attr = fk_fields_names_map.get(f.field_name, f.field_name)
        value = getattr(instance, attr)
        rmodel, rfield = f.related_model, f.remote_field.attname
        invalidate_dict(rmodel, {rfield: value}, using=using)


def invalidate_m2m(sender=None, instance=None, model=None, action=None, pk_set=None, reverse=None,
                   using=DEFAULT_DB_ALIAS, **kwargs):
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
                   if m2m.remote_field.through == sender)
    instance_column, model_column = m2m.m2m_column_name(), m2m.m2m_reverse_name()
    if reverse:
        instance_column, model_column = model_column, instance_column

    # TODO: optimize several invalidate_objs/dicts at once
    if action == 'pre_clear':
        objects = sender.objects.using(using).filter(**{instance_column: instance.pk})
        for obj in objects:
            invalidate_obj(obj, using=using)
    elif action in ('post_add', 'pre_remove'):
        # NOTE: we don't need to query through objects here,
        #       cause we already know all their meaningful attributes.
        for pk in pk_set:
            invalidate_dict(sender, {
                instance_column: instance.pk,
                model_column: pk
            }, using=using)


@once
def install_cacheops():
    """
    Installs cacheops by numerous monkey patches
    """
    monkey_mix(BaseManager, ManagerMixin)
    monkey_mix(models.QuerySet, QuerySetMixin)

    # Use app registry to introspect used apps
    from django.apps import apps

    # Install profile and signal handlers for any earlier created models
    for model in apps.get_models(include_auto_created=True):
        if family_has_profile(model):
            if not isinstance(model._default_manager, BaseManager):
                raise ImproperlyConfigured("Can't install cacheops for %s.%s model:"
                                           " non-django model class or manager is used."
                                            % (model._meta.app_label, model._meta.model_name))
            model._default_manager._install_cacheops(model)

            # Bind m2m changed handlers
            m2ms = (f for f in model._meta.get_fields(include_hidden=True) if f.many_to_many)
            for m2m in m2ms:
                rel = m2m if hasattr(m2m, 'through') else m2m.remote_field
                opts = rel.through._meta
                m2m_changed.connect(invalidate_m2m, sender=rel.through,
                                    dispatch_uid=(opts.app_label, opts.model_name))

    # Turn off caching in admin
    if apps.is_installed('django.contrib.admin'):
        from django.contrib.admin.options import ModelAdmin

        @monkey(ModelAdmin)
        def get_queryset(self, request):
            return get_queryset.original(self, request).nocache()

    # Make buffers/memoryviews pickleable to serialize binary field data
    import copyreg
    copyreg.pickle(memoryview, lambda b: (memoryview, (bytes(b),)))

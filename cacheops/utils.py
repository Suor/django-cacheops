# -*- coding: utf-8 -*-
import re
import json
import inspect
from funcy import memoize, compose, wraps, any, any_fn, select_values, make_lookuper
from funcy.py3 import lmapcat
from .cross import md5hex

from django.apps import apps
from django.db import models
from django.http import HttpRequest

from .conf import model_profile


# NOTE: we don't serialize this fields since their values could be very long
#       and one should not filter by their equality anyway.
NOT_SERIALIZED_FIELDS = (
    models.FileField,
    models.TextField, # One should not filter by long text equality
    models.BinaryField,
)


def model_family(model):
    """
    Returns a list of all proxy models, including subclasess, superclassses and siblings.
    """
    def class_tree(cls):
        return [cls] + lmapcat(class_tree, cls.__subclasses__())

    # NOTE: we also list multitable submodels here, we just don't care.
    #       Cacheops doesn't support them anyway.
    return class_tree(model._meta.concrete_model)


@memoize
def family_has_profile(cls):
    return any(model_profile, model_family(cls))


@make_lookuper
def table_to_model():
    d = {m._meta.db_table: m for m in apps.get_models(include_auto_created=True)}
    from django.db.migrations.recorder import MigrationRecorder
    d['django_migrations'] = MigrationRecorder.Migration
    return d


class MonkeyProxy(object):
    pass

def monkey_mix(cls, mixin):
    """
    Mixes a mixin into existing class.
    Does not use actual multi-inheritance mixins, just monkey patches methods.
    Mixin methods can call copies of original ones stored in `_no_monkey` proxy:

    class SomeMixin(object):
        def do_smth(self, arg):
            ... do smth else before
            self._no_monkey.do_smth(self, arg)
            ... do smth else after
    """
    assert not hasattr(cls, '_no_monkey'), 'Multiple monkey mix not supported'
    cls._no_monkey = MonkeyProxy()

    test = any_fn(inspect.isfunction, inspect.ismethoddescriptor)
    methods = select_values(test, mixin.__dict__)

    for name, method in methods.items():
        if hasattr(cls, name):
            setattr(cls._no_monkey, name, getattr(cls, name))
        setattr(cls, name, method)


@memoize
def stamp_fields(model):
    """
    Returns serialized description of model fields.
    """
    stamp = str(sorted((f.name, f.attname, f.db_column, f.__class__) for f in model._meta.fields))
    return md5hex(stamp)


### Cache keys calculation

def obj_key(obj):
    if isinstance(obj, models.Model):
        return '%s.%s.%s' % (obj._meta.app_label, obj._meta.model_name, obj.pk)
    elif inspect.isfunction(obj):
        factors = [obj.__module__, obj.__name__]
        # Really useful to ignore this while code still in development
        if hasattr(obj, '__code__') and not obj.__globals__.get('CACHEOPS_DEBUG'):
            factors.append(obj.__code__.co_firstlineno)
        return factors
    else:
        return str(obj)

def func_cache_key(func, args, kwargs, extra=None):
    """
    Calculate cache key based on func and arguments
    """
    factors = [func, args, kwargs, extra]
    return md5hex(json.dumps(factors, sort_keys=True, default=obj_key))

def view_cache_key(func, args, kwargs, extra=None):
    """
    Calculate cache key for view func.
    Use url instead of not properly serializable request argument.
    """
    if hasattr(args[0], 'build_absolute_uri'):
        uri = args[0].build_absolute_uri()
    else:
        uri = args[0]
    return 'v:' + func_cache_key(func, args[1:], kwargs, extra=(uri, extra))

def cached_view_fab(_cached):
    def force_render(response):
        if hasattr(response, 'render') and callable(response.render):
            response.render()
        return response

    def cached_view(*dargs, **dkwargs):
        def decorator(func):
            dkwargs['key_func'] = view_cache_key
            cached_func = _cached(*dargs, **dkwargs)(compose(force_render, func))

            @wraps(func)
            def wrapper(request, *args, **kwargs):
                assert isinstance(request, HttpRequest),                            \
                       "A view should be passed with HttpRequest as first argument"
                if request.method not in ('GET', 'HEAD'):
                    return func(request, *args, **kwargs)

                return cached_func(request, *args, **kwargs)

            if hasattr(cached_func, 'invalidate'):
                wrapper.invalidate = cached_func.invalidate
                wrapper.key = cached_func.key

            return wrapper
        return decorator
    return cached_view


### Whitespace handling for template tags

from django.utils.safestring import mark_safe

NEWLINE_BETWEEN_TAGS = mark_safe('>\n<')
SPACE_BETWEEN_TAGS = mark_safe('> <')

def carefully_strip_whitespace(text):
    text = re.sub(r'>\s*\n\s*<', NEWLINE_BETWEEN_TAGS, text)
    text = re.sub(r'>\s{2,}<', SPACE_BETWEEN_TAGS, text)
    return text

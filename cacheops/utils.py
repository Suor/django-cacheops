# -*- coding: utf-8 -*-
import re
from functools import wraps
import json
import inspect
import threading
import six
from funcy import memoize
from .cross import md5hex

import django
from django.db import models
from django.http import HttpRequest

from .conf import redis_client


# NOTE: we don't serialize this fields since their values could be very long
#       and one should not filter by their equality anyway.
NOT_SERIALIZED_FIELDS = (
    models.FileField,
    models.TextField, # One should not filter by long text equality
)
if hasattr(models, 'BinaryField'):
    NOT_SERIALIZED_FIELDS += (models.BinaryField,)


def non_proxy(model):
    while model._meta.proxy:
        # Every proxy model has exactly one non abstract parent model
        model = next(b for b in model.__bases__
                       if issubclass(b, models.Model) and not b._meta.abstract)
    return model


if django.VERSION < (1, 6):
    def get_model_name(model):
        return model._meta.module_name
else:
    def get_model_name(model):
        return model._meta.model_name


class MonkeyProxy(object):
    def __init__(self, cls):
        monkey_bases = [b._no_monkey for b in cls.__bases__ if hasattr(b, '_no_monkey')]
        for monkey_base in monkey_bases:
            self.__dict__.update(monkey_base.__dict__)


def monkey_mix(cls, mixin, methods=None):
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
    assert '_no_monkey' not in cls.__dict__, 'Multiple monkey mix not supported'
    cls._no_monkey = MonkeyProxy(cls)

    if methods is None:
        # NOTE: there no such thing as unbound method in Python 3, it uses naked functions,
        #       so we use some six based altering here
        isboundmethod = inspect.isfunction if six.PY3 else inspect.ismethod
        methods = inspect.getmembers(mixin, isboundmethod)
    else:
        methods = [(m, getattr(mixin, m)) for m in methods]

    for name, method in methods:
        if hasattr(cls, name):
            setattr(cls._no_monkey, name, getattr(cls, name))
        # NOTE: remember, there is no bound methods in Python 3
        setattr(cls, name, six.get_unbound_function(method))


@memoize
def stamp_fields(model):
    """
    Returns serialized description of model fields.
    """
    stamp = str([(f.name, f.attname, f.db_column, f.__class__) for f in model._meta.fields])
    return md5hex(stamp)


### Cache keys calculation

def func_cache_key(func, args, kwargs, extra=None):
    """
    Calculate cache key based on func and arguments
    """
    factors = [func.__module__, func.__name__, func.__code__.co_firstlineno, args, kwargs, extra]
    return md5hex(json.dumps(factors, sort_keys=True, default=str))

def view_cache_key(func, args, kwargs, extra=None):
    """
    Calculate cache key for view func.
    Use url instead of not properly serializable request argument.
    """
    uri = args[0].build_absolute_uri()
    return 'v:' + func_cache_key(func, args[1:], kwargs, extra=(uri, extra))

def cached_view_fab(_cached):
    def cached_view(*dargs, **dkwargs):
        def decorator(func):
            dkwargs['_get_key'] = view_cache_key
            cached_func = _cached(*dargs, **dkwargs)(func)

            @wraps(func)
            def wrapper(request, *args, **kwargs):
                assert isinstance(request, HttpRequest),                            \
                       "A view should be passed with HttpRequest as first argument"
                if request.method not in ('GET', 'HEAD'):
                    return func(request, *args, **kwargs)

                return cached_func(request, *args, **kwargs)
            return wrapper
        return decorator
    return cached_view


### Lua script loader

import os.path

STRIP_RE = re.compile(r'TOSTRIP.*/TOSTRIP', re.S)

@memoize
def load_script(name, strip=False):
    # TODO: strip comments
    filename = os.path.join(os.path.dirname(__file__), 'lua/%s.lua' % name)
    with open(filename) as f:
        code = f.read()
    if strip:
        code = STRIP_RE.sub('', code)
    return redis_client.register_script(code)


### Whitespace handling for template tags

from django.utils.safestring import mark_safe

NEWLINE_BETWEEN_TAGS = mark_safe('>\n<')
SPACE_BETWEEN_TAGS = mark_safe('> <')

def carefully_strip_whitespace(text):
    text = re.sub(r'>\s*\n\s*<', NEWLINE_BETWEEN_TAGS, text)
    text = re.sub(r'>\s{2,}<', SPACE_BETWEEN_TAGS, text)
    return text


# This will help mimic thread globals via dicts

def get_thread_id():
    return threading.current_thread().ident

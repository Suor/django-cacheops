import re
import json
import inspect
from funcy import memoize, compose, wraps, any, any_fn, select_values, mapcat

from django.db import models
from django.http import HttpRequest

from .conf import model_profile


def model_family(model):
    """
    The family is models sharing a database table, events on one should affect each other.

    We simply collect a list of all proxy models, including subclasess, superclasses and siblings.
    Two descendants of an abstract model are not family - they cannot affect each other.
    """
    if model._meta.abstract:  # No table - no family
        return set()

    @memoize
    def class_tree(cls):
        # NOTE: we also list multitable submodels here, we just don't care.
        #       Cacheops doesn't support them anyway.
        return {cls} | set(mapcat(class_tree, cls.__subclasses__()))

    table_bases = {b for b in model.__mro__ if issubclass(b, models.Model) and b is not models.Model
                   and not b._meta.proxy and not b._meta.abstract}
    family = set(mapcat(class_tree, table_bases))
    return {cls for cls in family if not cls._meta.abstract}

@memoize
def family_has_profile(cls):
    return any(model_profile, model_family(cls))


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
    def _stamp(field):
        name, class_name, *_ = field.deconstruct()
        return name, class_name, field.attname, field.column

    stamp = str(sorted(map(_stamp, model._meta.fields)))
    return md5hex(stamp)


### Cache keys calculation

def obj_key(obj):
    if isinstance(obj, models.Model):
        return '%s.%s.%s' % (obj._meta.app_label, obj._meta.model_name, obj.pk)
    elif hasattr(obj, 'build_absolute_uri'):
        return obj.build_absolute_uri()  # Only vary HttpRequest by uri
    elif inspect.isfunction(obj):
        factors = [obj.__module__, obj.__name__]
        # Really useful to ignore this while code still in development
        if hasattr(obj, '__code__') and not obj.__globals__.get('CACHEOPS_DEBUG'):
            factors.append(obj.__code__.co_firstlineno)
        return factors
    else:
        return str(obj)

def get_cache_key(*factors):
    return md5hex(json.dumps(factors, sort_keys=True, default=obj_key))

def cached_view_fab(_cached):
    def force_render(response):
        if hasattr(response, 'render') and callable(response.render):
            response.render()
        return response

    def cached_view(*dargs, **dkwargs):
        def decorator(func):
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
    def repl(m):
        return NEWLINE_BETWEEN_TAGS if '\n' in m.group(0) else SPACE_BETWEEN_TAGS
    text = re.sub(r'>\s{2,}<', repl, text)
    return text


### hashing helpers

import hashlib


class md5:
    def __init__(self, s=None):
        self.md5 = hashlib.md5()
        if s is not None:
            self.update(s)

    def update(self, s):
        return self.md5.update(s.encode('utf-8'))

    def hexdigest(self):
        return self.md5.hexdigest()


def md5hex(s):
    return md5(s).hexdigest()

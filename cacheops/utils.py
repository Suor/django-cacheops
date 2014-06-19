# -*- coding: utf-8 -*-
from operator import concat
from itertools import product
from functools import wraps, reduce
import inspect
import six
# Use Python 2 map here for now
from funcy.py2 import memoize, map, cat
from .cross import json, md5hex

import django
from django.db import models
from django.db.models.query import QuerySet
from django.db.models.sql import AND, OR
from django.db.models.sql.query import Query, ExtraWhere
from django.db.models.sql.where import EverythingNode, NothingNode
from django.db.models.sql.expressions import SQLEvaluator
# A new thing in Django 1.6
try:
    from django.db.models.sql.where import SubqueryConstraint
except ImportError:
    class SubqueryConstraint(object):
        pass
# A new things in Django 1.7
try:
    from django.db.models.lookups import Lookup, Exact, In, IsNull
except ImportError:
    class Lookup(object):
        pass
from django.http import HttpRequest

from .conf import redis_client


LONG_DISJUNCTION = 8


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


# NOTE: we don't serialize this fields since their values could be very long
#       and one should not filter by their equality anyway.
NOT_SERIALIZED_FIELDS = (
    models.FileField,
    models.TextField, # One should not filter by long text equality
)
if hasattr(models, 'BinaryField'):
    NOT_SERIALIZED_FIELDS += (models.BinaryField,) # Not possible to filter by it


def dnfs(qs):
    """
    Converts query condition tree into a DNF of eq conds.
    Separately for each alias.

    Any negations, conditions with lookups other than __exact or __in,
    conditions on joined models and subrequests are ignored.
    __in is converted into = or = or = ...
    """
    SOME = object()
    SOME_COND = (None, None, SOME, True)

    def negate(term):
        return (term[0], term[1], term[2], not term[3])

    def _dnf(where):
        """
        Constructs DNF of where tree consisting of terms in form:
            (alias, attribute, value, negation)
        meaning `alias.attribute = value`
         or `not alias.attribute = value` if negation is False

        Any conditions other then eq are dropped.
        """
        # Lookups appeared in Django 1.7
        if isinstance(where, Lookup):
            attname = where.lhs.target.attname
            # TODO: check of all of this are possible
            if isinstance(where.rhs, (QuerySet, Query, SQLEvaluator)):
                return [[SOME_COND]]
            # TODO: deal with transforms, aggregates and such in lhs
            elif isinstance(where, Exact):
                if isinstance(where.lhs.target, NOT_SERIALIZED_FIELDS):
                    return [[SOME_COND]]
                else:
                    return [[(where.lhs.alias, attname, where.rhs, True)]]
            elif isinstance(where, IsNull):
                return [[(where.lhs.alias, attname, None, where.rhs)]]
            elif isinstance(where, In) and len(where.rhs) < LONG_DISJUNCTION:
                return [[(where.lhs.alias, attname, v, True)] for v in where.rhs]
            else:
                return [[SOME_COND]]
        # Django 1.6 and earlier used tuples to encode conditions
        elif isinstance(where, tuple):
            constraint, lookup, annotation, value = where
            attname = attname_of(model, constraint.col)
            if isinstance(value, (QuerySet, Query, SQLEvaluator)):
                return [[SOME_COND]]
            elif lookup == 'exact':
                # TODO: check for non-serialized for both exact and in
                if isinstance(constraint.field, NOT_SERIALIZED_FIELDS):
                    return [[SOME_COND]]
                else:
                    return [[(constraint.alias, attname, value, True)]]
            elif lookup == 'isnull':
                return [[(constraint.alias, attname, None, value)]]
            elif lookup == 'in' and len(value) < LONG_DISJUNCTION:
                return [[(constraint.alias, attname, v, True)] for v in value]
            else:
                return [[SOME_COND]]
        elif isinstance(where, EverythingNode):
            return [[]]
        elif isinstance(where, NothingNode):
            return []
        elif isinstance(where, (ExtraWhere, SubqueryConstraint)):
            return [[SOME_COND]]
        elif len(where) == 0:
            return [[]]
        else:
            chilren_dnfs = map(_dnf, where.children)

            if len(chilren_dnfs) == 0:
                return [[]]
            elif len(chilren_dnfs) == 1:
                result = chilren_dnfs[0]
            else:
                # Just unite children joined with OR
                if where.connector == OR:
                    result = cat(chilren_dnfs)
                # Use Cartesian product to AND children
                else:
                    result = map(cat, product(*chilren_dnfs))

            # Negating and expanding brackets
            if where.negated:
                result = [map(negate, p) for p in product(*result)]

            return result

    def clean_conj(conj, for_alias):
        # "SOME" conds, negated conds and conds for other aliases should be stripped
        return [(attname, value) for alias, attname, value, negation in conj
                                 if value is not SOME and negation and alias == for_alias]

    def clean_dnf(tree, for_alias):
        cleaned = [clean_conj(conj, for_alias) for conj in tree]
        # Any empty conjunction eats up the rest
        # NOTE: a more elaborate DNF reduction is not really needed,
        #       just keep your querysets sane.
        if not all(cleaned):
            return [[]]
        # To keep all schemes the same we sort conjunctions
        return map(sorted, cleaned)

    def table_for(alias):
        if alias == main_alias:
            return model._meta.db_table
        else:
            return qs.query.alias_map[alias][0]

    where = qs.query.where
    model = qs.model
    main_alias = model._meta.db_table

    dnf = _dnf(where)
    aliases = set(alias for conj in dnf
                        for alias, _, _, _ in conj
                        if alias)
    aliases.add(main_alias)
    return [(table_for(alias), clean_dnf(dnf, alias)) for alias in aliases]


def attname_of(model, col, cache={}):
    if model not in cache:
        cache[model] = dict((f.db_column, f.attname) for f in model._meta.fields)
    return cache[model].get(col, col)


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

@memoize
def load_script(name):
    # TODO: strip comments
    filename = os.path.join(os.path.dirname(__file__), 'lua/%s.lua' % name)
    with open(filename) as f:
        code = f.read()
    return redis_client.register_script(code)


### Whitespace handling for template tags

import re
from django.utils.safestring import mark_safe

NEWLINE_BETWEEN_TAGS = mark_safe('>\n<')
SPACE_BETWEEN_TAGS = mark_safe('> <')

def carefully_strip_whitespace(text):
    text = re.sub(r'>\s*\n\s*<', NEWLINE_BETWEEN_TAGS, text)
    text = re.sub(r'>\s{2,}<', SPACE_BETWEEN_TAGS, text)
    return text

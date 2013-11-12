# -*- coding: utf-8 -*-
from operator import concat, itemgetter
from itertools import product, imap
from inspect import getmembers, ismethod
import hashlib

import django
from django.db.models import Model
from django.db.models.query import QuerySet
from django.db.models.sql import AND, OR
from django.db.models.sql.query import Query, ExtraWhere


LONG_DISJUNCTION = 8


def non_proxy(model):
    while model._meta.proxy:
        # Every proxy model has exactly one non abstract parent model
        model = next(b for b in model.__bases__ if issubclass(b, Model) and not b._meta.abstract)
    return model


if django.VERSION < (1, 6):
    def get_model_name(model):
        return '%s.%s' % (model._meta.app_label, model._meta.module_name)
else:
    def get_model_name(model):
        return '%s.%s' % (model._meta.app_label, model._meta.model_name)


class MonkeyProxy(object):
    def __init__(self, cls):
        monkey_bases = tuple(b._no_monkey for b in cls.__bases__ if hasattr(b, '_no_monkey'))
        for monkey_base in monkey_bases:
            for name, value in monkey_base.__dict__.iteritems():
                setattr(self, name, value)


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
        methods = getmembers(mixin, ismethod)
    else:
        methods = [(m, getattr(mixin, m)) for m in methods]

    for name, method in methods:
        if hasattr(cls, name):
            setattr(cls._no_monkey, name, getattr(cls, name))
        setattr(cls, name, method.im_func)


# Some special subconditions that don't provide dnf narrowing
from django.db.models.sql.where import EverythingNode, NothingNode
dnfless_subconds = (ExtraWhere, EverythingNode, NothingNode)

# A new thing in Django 1.6 that should be ignored for dnf purposes
try:
    from django.db.models.sql.where import SubqueryConstraint
    dnfless_subconds += (SubqueryConstraint,)
except ImportError:
    pass

def dnf(qs):
    """
    Converts sql condition tree to DNF.

    Any negations, conditions with lookups other than __exact or __in,
    conditions on joined models and subrequests are ignored.
    __in is converted into = or = or = ...
    """
    def negate(el):
        return (el[0], el[1], not el[2])

    def strip_negates(conj):
        return [term[:2] for term in conj if term[2]]

    def _dnf(where):
        if isinstance(where, tuple):
            constraint, lookup, annotation, value = where
            if constraint.alias != alias or isinstance(value, (QuerySet, Query)):
                return [[]]
            elif lookup == 'exact':
                # attribute, value, negation
                return [[(attname_of(model, constraint.col), value, True)]]
            elif lookup == 'isnull':
                return [[(attname_of(model, constraint.col), None, value)]]
            elif lookup == 'in' and len(value) < LONG_DISJUNCTION:
                return [[(attname_of(model, constraint.col), v, True)] for v in value]
            else:
                return [[]]
        elif isinstance(where, dnfless_subconds):
            return [[]]
        elif len(where) == 0:
            return None
        else:
            chilren_dnfs = filter(None, imap(_dnf, where.children))

            if len(chilren_dnfs) == 0:
                return None
            elif len(chilren_dnfs) == 1:
                result = chilren_dnfs[0]
            else:
                # Just unite children joined with OR
                if where.connector == OR:
                    result = reduce(concat, chilren_dnfs)
                # Use Cartesian product to AND children
                else:
                    result = [reduce(concat, p) for p in product(*chilren_dnfs)]

            # Negating and expanding brackets
            if where.negated:
                result = [map(negate, p) for p in product(*result)]

            return result

    where = qs.query.where
    model = qs.model
    alias = model._meta.db_table

    result = _dnf(where)
    if result is None:
        return [[]]
    # Cutting out negative terms and negation itself
    result = map(strip_negates, result)
    # Any empty conjunction eats up the rest
    # NOTE: a more elaborate DNF reduction is not really needed,
    #       just keep your querysets sane.
    if not all(result):
        return [[]]
    return result


def attname_of(model, col, cache={}):
    if model not in cache:
        cache[model] = dict((f.db_column, f.attname) for f in model._meta.fields)
    return cache[model].get(col, col)


def conj_scheme(conj):
    """
    Return a scheme of conjunction.
    Which is just a sorted tuple of field names.
    """
    return tuple(sorted(imap(itemgetter(0), conj)))


def stamp_fields(model, cache={}):
    """
    Returns serialized description of model fields.
    """
    if model not in cache:
        stamp = str([(f.name, f.attname, f.db_column, f.__class__) for f in model._meta.fields])
        cache[model] = hashlib.md5(stamp).hexdigest()
    return cache[model]


import re
from django.utils.safestring import mark_safe

NEWLINE_BETWEEN_TAGS = mark_safe('>\n<')
SPACE_BETWEEN_TAGS = mark_safe('> <')

def carefully_strip_whitespace(text):
    text = re.sub(r'>\s*\n\s*<', NEWLINE_BETWEEN_TAGS, text)
    text = re.sub(r'>\s{2,}<', SPACE_BETWEEN_TAGS, text)
    return text

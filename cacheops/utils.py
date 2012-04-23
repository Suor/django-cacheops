# -*- coding: utf-8 -*-
from operator import concat, itemgetter
from itertools import product, imap
from inspect import getmembers, ismethod

from django.db.models.query import QuerySet
from django.db.models.sql import AND, OR
from django.db.models.sql.query import ExtraWhere


LONG_DISJUNCTION = 8


def get_model_name(model):
    return '%s.%s' % (model._meta.app_label, model._meta.module_name)


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
            if constraint.alias != alias or isinstance(value, QuerySet):
                return [[]]
            elif lookup == 'exact':
                return [[(attname_of(model, constraint.col), value, True)]]
            elif lookup == 'in' and len(value) < LONG_DISJUNCTION:
                return [[(attname_of(model, constraint.col), v, True)] for v in value]
            else:
                return [[]]
        elif isinstance(where, ExtraWhere):
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

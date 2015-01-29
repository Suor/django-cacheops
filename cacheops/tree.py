# -*- coding: utf-8 -*-
from itertools import product
# Use Python 2 map here for now
from funcy.py2 import map, cat

from django.db.models.query import QuerySet
from django.db.models.sql import OR
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

from .utils import NOT_SERIALIZED_FIELDS


LONG_DISJUNCTION = 8


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
            # If where.lhs don't refer to a field then don't bother
            if not hasattr(where.lhs, 'target'):
                return [[SOME_COND]]
            # TODO: check if all of this are possible
            if isinstance(where.rhs, (QuerySet, Query, SQLEvaluator)):
                return [[SOME_COND]]

            attname = where.lhs.target.attname
            if isinstance(where, Exact):
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

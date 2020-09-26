from itertools import product
from funcy import group_by, join_with, lcat, lmap

from django.db.models import Subquery
from django.db.models.query import QuerySet
from django.db.models.sql import OR
from django.db.models.sql.query import Query, ExtraWhere
from django.db.models.sql.where import NothingNode, SubqueryConstraint
from django.db.models.lookups import Lookup, Exact, In, IsNull
from django.db.models.expressions import BaseExpression, Exists

from .conf import settings


def dnfs(qs):
    """
    Converts query condition tree into a DNF of eq conds.
    Separately for each alias.

    Any negations, conditions with lookups other than __exact or __in,
    conditions on joined models and subrequests are ignored.
    __in is converted into = or = or = ...
    """
    SOME = object()
    SOME_TREE = [[(None, None, SOME, True)]]

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
        if isinstance(where, Lookup):
            # If where.lhs don't refer to a field then don't bother
            if not hasattr(where.lhs, 'target'):
                return SOME_TREE
            # Don't bother with complex right hand side either
            if isinstance(where.rhs, (QuerySet, Query, BaseExpression)):
                return SOME_TREE
            # Skip conditions on non-serialized fields
            if isinstance(where.lhs.target, settings.CACHEOPS_SKIP_FIELDS):
                return SOME_TREE

            attname = where.lhs.target.attname
            if isinstance(where, Exact):
                return [[(where.lhs.alias, attname, where.rhs, True)]]
            elif isinstance(where, IsNull):
                return [[(where.lhs.alias, attname, None, where.rhs)]]
            elif isinstance(where, In) and len(where.rhs) < settings.CACHEOPS_LONG_DISJUNCTION:
                return [[(where.lhs.alias, attname, v, True)] for v in where.rhs]
            else:
                return SOME_TREE
        elif isinstance(where, NothingNode):
            return []
        elif isinstance(where, (ExtraWhere, SubqueryConstraint, Exists)):
            return SOME_TREE
        elif len(where) == 0:
            return [[]]
        else:
            chilren_dnfs = lmap(_dnf, where.children)

            if len(chilren_dnfs) == 0:
                return [[]]
            elif len(chilren_dnfs) == 1:
                result = chilren_dnfs[0]
            else:
                # Just unite children joined with OR
                if where.connector == OR:
                    result = lcat(chilren_dnfs)
                # Use Cartesian product to AND children
                else:
                    result = lmap(lcat, product(*chilren_dnfs))

            # Negating and expanding brackets
            if where.negated:
                result = [lmap(negate, p) for p in product(*result)]

            return result

    def clean_conj(conj, for_alias):
        conds = {}
        for alias, attname, value, negation in conj:
            # "SOME" conds, negated conds and conds for other aliases should be stripped
            if value is not SOME and negation and alias == for_alias:
                # Conjs with fields eq 2 different values will never cause invalidation
                if attname in conds and conds[attname] != value:
                    return None
                conds[attname] = value
        return conds

    def clean_dnf(tree, aliases):
        cleaned = [clean_conj(conj, alias) for conj in tree for alias in aliases]
        # Remove deleted conjunctions
        cleaned = [conj for conj in cleaned if conj is not None]
        # Any empty conjunction eats up the rest
        # NOTE: a more elaborate DNF reduction is not really needed,
        #       just keep your querysets sane.
        if not all(cleaned):
            return [{}]
        return cleaned

    def query_dnf(query):
        def table_for(alias):
            if alias == main_alias:
                return alias
            return query.alias_map[alias].table_name

        dnf = _dnf(query.where)

        # NOTE: we exclude content_type as it never changes and will hold dead invalidation info
        main_alias = query.model._meta.db_table
        aliases = {alias for alias, join in query.alias_map.items()
                   if query.alias_refcount[alias]} \
                | {main_alias} - {'django_content_type'}
        tables = group_by(table_for, aliases)
        return {table: clean_dnf(dnf, table_aliases) for table, table_aliases in tables.items()}

    if qs.query.combined_queries:
        dnfs_ = join_with(lcat, (query_dnf(q) for q in qs.query.combined_queries))
    else:
        dnfs_ = query_dnf(qs.query)

    # Add any subqueries used for annotation
    if qs.query.annotations:
        subqueries = (
            # Django 3.0+ sets Subquery.query
            query_dnf(getattr(q, 'query', None) or getattr(q, 'queryset').query)
            for q in qs.query.annotations.values()
            if type(q) is Subquery
        )
        dnfs_.update(join_with(lcat, subqueries))

    return dnfs_

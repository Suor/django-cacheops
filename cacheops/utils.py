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
    Процедура миксина в уже существующий класс в стиле обезьяних патчей.
    На самом деле не делает обычный миксин, т.е. не использует множественное наследование,
        а просто переписывает методы класса методами миксина поверх.
    Методы миксина могут вызывать оригинальные методы с помощью специального прокси хранящегося
    в классовом аттрибуте _no_monkey:

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



def dnf(where, alias):
    """
    Приводит дерево SQL-запроса к дизъюнктивной форме.
    Как правило, для queryset-а составленого не долбоёбом, получим ДНФ.

    Отрицательные условия и условия с lookup отличными от exact и in превращаются
    в пустые (всегда истинные) конъюнкции. Условия на приджойненые таблицы тоже.
    Lookup in разворачивается в = or = or = ...
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
                return [[(constraint.col, value, True)]] # колонка, значение, отрицание
            elif lookup == 'in' and len(value) < LONG_DISJUNCTION:
                return [[(constraint.col, v, True)] for v in value]
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
                # В случае OR просто объединяем полученные дизъюнкции
                if where.connector == OR:
                    result = reduce(concat, chilren_dnfs)
                # В случае AND выполняем декртово произведение списков и объединяем конъюнкции
                else:
                    result = [reduce(concat, p) for p in product(*chilren_dnfs)]

            # Если у нас стоит отрицание, то выворачиваем форму
            if where.negated:
                result = [map(negate, p) for p in product(*result)]

            return result

    result = _dnf(where)
    if result is None:
        return [[]]
    # Вырезаем отрицательные термы и само отрицание
    result = map(strip_negates, result)
    # Если есть хоть одна пустая конъюнкция она поглощает остальные
    if not all(result):
        return [[]]
    return result


def conj_scheme(conj):
    """
    Возвращает схему элементарной конъюнкции.
    В текущей реализации схема - это отсортированная последовательность полей
    """
    return tuple(sorted(imap(itemgetter(0), conj)))

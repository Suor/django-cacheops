from __future__ import absolute_import
import inspect

from django.template.base import TagHelperNode, parse_bits
from django.template import Library

import cacheops
from cacheops.utils import carefully_strip_whitespace


__all__ = ['invalidate_fragment']


register = Library()


def decorator_tag(func):
    name = func.__name__
    params, varargs, varkw, defaults = inspect.getargspec(func)

    class HelperNode(TagHelperNode):
        def __init__(self, takes_context, args, kwargs, nodelist=None):
            super(HelperNode, self).__init__(takes_context, args, kwargs)
            self.nodelist = nodelist

        def render(self, context):
            args, kwargs = self.get_resolved_arguments(context)
            decorator = func(*args, **kwargs)
            render = _make_render(context, self.nodelist)
            return decorator(render)()

    def _compile(parser, token):
        # content
        nodelist = parser.parse(('end' + name,))
        parser.delete_first_token()

        # args
        bits = token.split_contents()[1:]
        args, kwargs = parse_bits(parser, bits, params, varargs, varkw, defaults,
                                  takes_context=None, name=name)
        return HelperNode(False, args, kwargs, nodelist)

    register.tag(name=name, compile_function=_compile)
    return func


def _make_render(context, nodelist):
    def render():
        # TODO: make this cache preparation configurable
        return carefully_strip_whitespace(nodelist.render(context))
    return render


@decorator_tag
def cached(timeout, fragment_name, *extra):
    return cacheops.cached(timeout=timeout, extra=(fragment_name,) + extra)


def invalidate_fragment(fragment_name, *extra):
    render = _make_render(None, None)
    cached(None, fragment_name, *extra)(render).invalidate()


@decorator_tag
def cached_as(queryset, timeout, fragment_name, *extra):
    return cacheops.cached_as(queryset, timeout=timeout, extra=(fragment_name,) + extra)

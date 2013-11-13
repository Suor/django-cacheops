from __future__ import absolute_import
import inspect

from django.template.base import TagHelperNode, parse_bits
from django.template import Library


register = Library()


def tag_helper(func):
    name = func.__name__
    params, varargs, varkw, defaults = inspect.getargspec(func)

    class HelperNode(TagHelperNode):
        def __init__(self, takes_context, args, kwargs, nodelist=None):
            super(HelperNode, self).__init__(takes_context, args, kwargs)
            self.nodelist = nodelist

        def render(self, context):
            args, kwargs = self.get_resolved_arguments(context)
            return func(context, self.nodelist, *args, **kwargs)

    def _compile(parser, token):
        # content
        nodelist = parser.parse(('end' + name,))
        parser.delete_first_token()

        # args
        bits = token.split_contents()[1:]
        args, kwargs = parse_bits(parser, bits, params[2:], varargs, varkw, defaults,
                                  takes_context=None, name=name)
        return HelperNode(False, args, kwargs, nodelist)

    register.tag(name=name, compile_function=_compile)
    return func


import cacheops
from cacheops.utils import carefully_strip_whitespace

@tag_helper
def cached(context, nodelist, timeout, fragment_name, *extra):
    @cacheops.cached(timeout=timeout, extra=(fragment_name,) + extra)
    def _handle_tag():
        # TODO: make this cache preparation configurable
        return carefully_strip_whitespace(nodelist.render(context))

    return _handle_tag()

@tag_helper
def cached_as(context, nodelist, queryset, timeout, fragment_name, *extra):
    @cacheops.cached_as(queryset, timeout=timeout, extra=(fragment_name,) + extra)
    def _handle_tag():
        # TODO: make this cache preparation configurable
        return carefully_strip_whitespace(nodelist.render(context))

    return _handle_tag()
